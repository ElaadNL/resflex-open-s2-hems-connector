# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2 pairing server — state and core logic only.

HTTP views live in views.py to keep this file focused.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aiohttp import web

from ..const import (
    MAX_RATE_LIMIT_KEYS,
    PAIRING_ATTEMPT_TIMEOUT_SECONDS,
    PENDING_TOKEN_TIMEOUT_SECONDS,
    RATE_LIMIT_WINDOW_SECONDS,
    REQUEST_PAIRING_RATE_LIMIT,
)
from .pairing_models import (
    CommunicationProtocol,
    Deployment,
    EndpointDescription,
    HmacHashingAlgorithm,
    PairingErrorMessage,
    PairingInfo,
    PairingRequest,
    PreparePairingRequest,
    Role,
    S2NodeDescription,
)
from .pairing_token import PairingToken
from .token import Token, generate_pairing_attempt_id


class _PairedNode:
    def __init__(self, access_token: Token, node_description: S2NodeDescription, node_id: UUID, endpoint_description: EndpointDescription | None = None, certificate_fingerprint: bytes | None = None):
        self.node_id = node_id
        self.node_description = node_description
        self.access_token = access_token
        self.endpoint_description = endpoint_description
        self.active_comm_token = None
        self.certificate_fingerprint = certificate_fingerprint  # SHA256 of CEM leaf cert at pairing time


class _PendingToken:
    def __init__(self, node: _PairedNode, new_access_token: Token, timestamp: datetime,
                 new_comm_token: Token | None = None):
        self.node = node
        self.new_access_token = new_access_token
        self.new_comm_token = new_comm_token
        self.timestamp = timestamp

    def is_expired(self) -> bool:
        return datetime.now(UTC) - self.timestamp > timedelta(seconds=PENDING_TOKEN_TIMEOUT_SECONDS)


class PairingAttempt:
    """Tracks a single pairing attempt with its 15-second timeout."""

    def __init__(self, attempt_id: str, client_node_id: UUID,
                 server_hmac_challenge: str | None = None,
                 pairing_token: str | None = None):
        self.attempt_id = attempt_id
        self.client_node_id = client_node_id
        self.server_hmac_challenge = server_hmac_challenge
        self.pairing_token = pairing_token
        self.created_at = datetime.now(UTC)

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=PAIRING_ATTEMPT_TIMEOUT_SECONDS)

    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


class PairingServer:
    def __init__(
        self,
        role: Role,
        node_description: S2NodeDescription,
        node_id: UUID | None = None,
        endpoint_description: EndpointDescription | None = None,
        supported_communication_protocols: list[CommunicationProtocol] | None = None,
        supported_s2_message_versions: list[str] | None = None,
        certificate_fingerprint: bytes | None = None,
        pairing_token: str | None = None,
    ):
        self.role = role
        self.node_description = node_description
        self.node_id = node_id or node_description.id
        self.endpoint_description = endpoint_description
        self.supported_communication_protocols = supported_communication_protocols or [CommunicationProtocol.WEBSOCKET]
        self.supported_s2_message_versions = supported_s2_message_versions or []
        self.certificate_fingerprint = certificate_fingerprint  # SHA256 hash of DER-encoded leaf cert
        self.paired_nodes: dict[UUID, _PairedNode] = {}
        self.pairing: dict[UUID, Token] = {}
        self.pairing_token: Token | None = PairingToken.pairing_token(pairing_token) if pairing_token else None
        self._pairing_token_is_fixed = pairing_token is not None
        self.pending_tokens: list[_PendingToken] = []
        self.pairing_attempts: dict[str, PairingAttempt] = {}
        self.active_connections: dict[UUID, web.WebSocketResponse] = {}
        self._longpoll_events: dict[UUID, asyncio.Event] = {}  # clientNodeId -> event for long-poll wake-up
        # Long-poll data reported by RMs (node/endpoint desc, error message)
        self.longpoll_nodes: dict[UUID, S2NodeDescription] = {}
        self.longpoll_endpoints: dict[UUID, EndpointDescription] = {}
        self.longpoll_errors: dict[UUID, str] = {}
        # Rate limiting for preparePairing and requestPairing: nodeId -> list of attempt timestamps (per-node as spec requires)
        self._request_pairing_attempts: dict[str, list[datetime]] = {}

    def verify_certificate(self, node_id: UUID) -> bool:
        """Check that the current TLS certificate matches the one pinned at pairing time.

        Trusting a self-signed root certificate requires the client
        to pin the CA certificate after successful pairing.  The CEM, as
        communication server, must detect when its own certificate has changed
        so it can warn the operator that re-pairing is needed.

        Returns True when the certificate is consistent (or no fingerprint was
        pinned), False when it has changed.
        """
        node = self.paired_nodes.get(node_id)
        if node is None:
            return True  # not paired yet, nothing to check
        pinned = node.certificate_fingerprint
        if pinned is None:
            return True  # no fingerprint was stored (pre-pinning pairing)
        if self.certificate_fingerprint is None:
            return True  # current deployment has no cert fingerprint configured
        return pinned == self.certificate_fingerprint

    def generate_pairing_info(self, pairing_url: str) -> PairingInfo:
        """Generate a single-use pairing token and return display info.
        
        If a fixed pairing_token was provided at construction (e.g. --pairing-token),
        reuse it instead of generating a new random token.
        """
        if not self._pairing_token_is_fixed or self.pairing_token is None or self.pairing_token.expired():
            self.pairing_token = PairingToken.pairing_token()
        return PairingInfo(
            pairingUrl=pairing_url,
            token=self.pairing_token.value,
            validUntil=self.pairing_token.valid_till,
        )

    def _verify_can_pair(self, body: PreparePairingRequest | PairingRequest) -> web.Response | None:
        """Return an error response if the target node, roles, S2 versions, communication
        protocols, or HMAC algorithms are incompatible, else None."""
        if isinstance(body, PreparePairingRequest) and body.serverNodeId != self.node_id:
            return _error(400, PairingErrorMessage.NODE_NOT_FOUND)
        if body.clientNodeDescription.role == self.role.value:
            return _error(400, PairingErrorMessage.INVALID_COMBINATION_OF_ROLES)
        # S2 message version / comm protocol / HMAC compat: only for PairingRequest
        if isinstance(body, PairingRequest):
            if not set(body.supportedS2MessageVersions).intersection(
                self.supported_s2_message_versions
            ) and not body.forcePairing:
                return _error(400, PairingErrorMessage.INCOMPATIBLE_S2_MESSAGE_VERSIONS)
            if not {
                p.value for p in body.supportedCommunicationProtocols
            }.intersection(p.value for p in self.supported_communication_protocols):
                return _error(400, PairingErrorMessage.INCOMPATIBLE_COMMUNICATION_PROTOCOLS)
            if HmacHashingAlgorithm.SHA256 not in body.supportedHmacHashingAlgorithms:
                return _error(400, PairingErrorMessage.INCOMPATIBLE_HMAC_HASHING_ALGORITHMS)
        return None

    def allow_pairing_request(self, node_key: str) -> bool:
        """Record a pairing request for node_key and say whether it is within the rate limit."""
        now = datetime.now(UTC)
        window_start = now.timestamp() - RATE_LIMIT_WINDOW_SECONDS

        # drop records whose attempts have all aged out, so idle keys do not accumulate
        for key, stamps in list(self._request_pairing_attempts.items()):
            recent = [t for t in stamps if t.timestamp() > window_start]
            if recent:
                self._request_pairing_attempts[key] = recent
            else:
                del self._request_pairing_attempts[key]

        attempts = self._request_pairing_attempts.get(node_key, [])
        # already at the limit: reject without recording, so the list stays bounded
        if len(attempts) >= REQUEST_PAIRING_RATE_LIMIT:
            return False
        # cap the tracked keys so rotating the key cannot grow the table
        if not attempts and len(self._request_pairing_attempts) >= MAX_RATE_LIMIT_KEYS:
            return False

        attempts.append(now)
        self._request_pairing_attempts[node_key] = attempts
        return True

    def begin_pairing_attempt(self, client_node_id: UUID,
                              server_hmac_challenge: str | None = None,
                              pairing_token: str | None = None) -> PairingAttempt:
        """Create a new pairing attempt, prune expired ones, and return it."""
        self._prune_expired_attempts()
        attempt_id = generate_pairing_attempt_id()
        attempt = PairingAttempt(attempt_id, client_node_id,
                                 server_hmac_challenge=server_hmac_challenge,
                                 pairing_token=pairing_token)
        self.pairing_attempts[attempt_id] = attempt
        return attempt

    def get_pairing_attempt(self, attempt_id: str) -> PairingAttempt | None:
        """Return the PairingAttempt if valid and not expired, else None."""
        attempt = self.pairing_attempts.get(attempt_id)
        if attempt is None:
            return None
        if attempt.is_expired():
            del self.pairing_attempts[attempt_id]
            return None
        return attempt

    def _prune_expired_attempts(self) -> None:
        """Remove all expired pairing attempts."""
        expired = [aid for aid, a in self.pairing_attempts.items() if a.is_expired()]
        for aid in expired:
            del self.pairing_attempts[aid]

    @property
    def certificate_fingerprint_hex(self) -> str | None:
        """SHA256 fingerprint of the TLS leaf certificate as colon-separated hex.

        For example, a configured SHA-256 fingerprint is returned as
        "AA:BB:CC:...".  Returns None if no certificate is configured.
        """
        if self.certificate_fingerprint is None:
            return None
        return ":".join(f"{b:02X}" for b in self.certificate_fingerprint).upper()

    def compute_hmac_challenge_response(
        self, challenge_b64: str, pairing_token: str, *, deployment: Deployment | str = Deployment.LAN, server_domain: str = ""
    ) -> str:
        """Compute the HMAC challenge-response

        For LAN: ``HMAC-SHA256(challenge, token || fingerprint)`` where the TLS
        certificate fingerprint is included in the HMAC message.
        For WAN: ``HMAC-SHA256(challenge, token || domain)``.

        Returns the Base64-encoded response.
        """
        challenge = base64.b64decode(challenge_b64)
        token_bytes = pairing_token.encode("ascii")

        if isinstance(deployment, str):
            deployment = Deployment(deployment)
        if deployment == Deployment.LAN:
            if self.certificate_fingerprint is None:
                raise ValueError("Certificate fingerprint required for LAN HMAC challenge-response")
            message = token_bytes + self.certificate_fingerprint
        else:
            message = token_bytes + server_domain.encode("ascii")

        response = hmac.digest(challenge, message, hashlib.sha256)
        return base64.b64encode(response).decode("ascii")

    def generate_hmac_challenge(self) -> str:
        """Generate a random HMAC challenge (32 bytes, Base64-encoded)"""
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    def verify_hmac_challenge_response(
        self, challenge_b64: str, expected_response_b64: str, pairing_token: str,
        *, deployment: Deployment | str = Deployment.LAN, server_domain: str = ""
    ) -> bool:
        """Verify that expected_response_b64 matches the computed HMAC response.

        Returns True if the client's response is correct.
        """
        computed = self.compute_hmac_challenge_response(
            challenge_b64, pairing_token, deployment=deployment, server_domain=server_domain,
        )
        return hmac.compare_digest(computed, expected_response_b64)

    def begin_pending_access_token(self, node_id: UUID) -> tuple[Token, _PendingToken]:
        """Generate a new pending access token for the given paired node.

        Prunes expired pending tokens. Returns (new_token, pending_entry).
        """
        node = self.paired_nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found in paired_nodes")

        # Prune expired tokens and any previous pending entry for this node
        self.pending_tokens = [
            p for p in self.pending_tokens
            if not p.is_expired() and p.node.node_id != node_id
        ]

        new_access_token = Token.access_token()
        pending = _PendingToken(node, new_access_token, datetime.now(UTC))
        self.pending_tokens.append(pending)
        return new_access_token, pending

    def activate_pending_access_token(
        self, pending_access_token_str: str, *, comm_token: Token
    ) -> _PendingToken | None:
        """Activate a pending access token: make it active, invalidate old, attach comm token.

        Returns the _PendingToken if activation succeeded, None if not found or expired.
        """
        for p in self.pending_tokens:
            if p.new_access_token.check(pending_access_token_str) and not p.is_expired():
                # Store comm token, activate new access token
                p.new_comm_token = comm_token
                p.node.access_token = p.new_access_token
                # Keep this entry (WS handler needs it) and prune expired ones
                self.pending_tokens = [
                    pt for pt in self.pending_tokens
                    if pt is p or not pt.is_expired()
                ]
                return p
        # Clean up expired entries even if not found
        self.pending_tokens = [p for p in self.pending_tokens if not p.is_expired()]
        return None

    def unpair_node(self, node_id: UUID) -> web.WebSocketResponse | None:
        """Remove all info for the node and return the WS connection."""
        del self.paired_nodes[node_id]  # raises KeyError if not found
        self.pending_tokens = [
            p for p in self.pending_tokens if p.node.node_id != node_id
        ]
        return self.active_connections.pop(node_id, None)

    # -- long-polling /waitForPairing --

    def longpoll_create_event(self, client_node_id: UUID) -> asyncio.Event:
        """Register a waiter for this node, releasing any older one.

        A client that asks again before the hold expires would otherwise leave several
        waiters behind, and a trigger could land on one that nobody is reading any more.
        """
        previous = self._longpoll_events.get(client_node_id)
        if previous is not None:
            previous.set()  # no action attached, so the older poll just answers 204
        event = asyncio.Event()
        self._longpoll_events[client_node_id] = event
        return event

    def longpoll_remove_event(self, client_node_id: UUID, event: asyncio.Event | None = None) -> None:
        """Drop the waiter, but only while it is still the current one."""
        if event is None or self._longpoll_events.get(client_node_id) is event:
            self._longpoll_events.pop(client_node_id, None)

    def longpoll_trigger(self, client_node_id: UUID, action: str) -> bool:
        """Trigger a long-poll action for the given client node.

        Returns True if an event was triggered, False if no client is polling.
        """
        event = self._longpoll_events.get(client_node_id)
        if event is None or event.is_set():
            return False
        # Store the action on the event so the waiter can read it
        event._action = action  # type: ignore[attr-defined]
        event.set()
        return True

    def initiate_pairing(self, client_node_id: UUID, pairing_token: str | None = None) -> bool:
        """Initiate pairing with a long-polling RM (CEM is the initiator).

        Store the RM's pairing token for the later HMAC check, then trigger the
        requestPairing action. True if the RM was polling.
        """
        if pairing_token is not None:
            self.pairing[client_node_id] = PairingToken.pairing_token(pairing_token)
        return self.longpoll_trigger(client_node_id, "requestPairing")

    def longpoll_record_entry(
        self,
        client_node_id: UUID,
        node_description: S2NodeDescription | None = None,
        endpoint_description: EndpointDescription | None = None,
        error_message: str | None = None,
    ) -> None:
        """Store node/endpoint descriptions and errors reported by a polling RM."""
        if node_description is not None:
            self.longpoll_nodes[client_node_id] = node_description
        if endpoint_description is not None:
            self.longpoll_endpoints[client_node_id] = endpoint_description
        if error_message is not None:
            self.longpoll_errors[client_node_id] = error_message


def _error(status: int, message: PairingErrorMessage | str, additional_info: str | None = None) -> web.Response:
    """Build a JSON error response."""
    body: dict = {"errorMessage": message}
    if additional_info:
        body["additionalInfo"] = additional_info
    return web.Response(status=status, body=json.dumps(body), content_type="application/json")

