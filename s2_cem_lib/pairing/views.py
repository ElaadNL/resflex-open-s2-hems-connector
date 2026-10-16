# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""HTTP views for the S2 Pairing API.

Pairing API (/pairing/ , preparePairing, cancelPreparePairing, requestPairing)

Views read ``request.app["s2_ctx"]`` for the pairing server and ports.

Auth failures are raised so middleware sees the attempt.
"""
from __future__ import annotations

import asyncio
import logging
import re
from uuid import UUID

from aiohttp import web

from ..const import (
    MAX_PENDING_PAIRING_OFFERS,
    PAIRING_TOKEN_TIMEOUT_MINUTES,
    S2_CONNECT_API_VERSION,
    S2_CONNECTION_PATH,
    S2_PAIRING_PATH,
)
from ..s2_cem.context import S2Context
from .pairing_models import (
    ConnectionDetailsRequest,
    ConnectionDetailsResponse,
    Deployment,
    FinalizePairingRequest,
    HmacHashingAlgorithm,
    PairingErrorMessage,
    PairingRequest,
    PairingResponse,
    PostConnectionDetailsRequest,
    PreparePairingRequest,
    WaitForPairingAction,
    WaitForPairingEntry,
)
from .pairing_server import _error, _PairedNode
from .pairing_token import PairingToken
from .persistence import save_paired_nodes
from .token import Token

_503 = web.Response(status=503, reason="Engine not initialized")

_BEARER_RE = re.compile(r"(?i)bearer\s+(\S+)")

_LOGGER = logging.getLogger(__name__)


def _get_ctx(request: web.Request) -> S2Context | None:
    return request.app.get("s2_ctx")


_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~])")


def _escape_markdown(text: str) -> str:
    """Backslash-escape markdown metacharacters so device-supplied text is shown literally."""
    return _MARKDOWN_CHARS.sub(r"\\\1", text)


def _extract_bearer(request: web.Request) -> str | None:
    """Extract Bearer token from Authorization header, or None."""
    auth = request.headers.get("Authorization", "")
    m = _BEARER_RE.match(auth)
    return m.group(1) if m else None


def get_views() -> list[tuple[str, type[web.View]]]:
    """Return (path, ViewClass) pairs for the Pairing API."""
    base = S2_PAIRING_PATH
    ver = f"{base}/{S2_CONNECT_API_VERSION}"
    return [
        (f"{base}", PairingApiVersionView),
        (f"{ver}/endpoint", EndpointView),
        (f"{ver}/nodes", NodesView),
        (f"{ver}/preparePairing", PreparePairingView),
        (f"{ver}/cancelPreparePairing", CancelPreparePairingView),
        (f"{ver}/requestPairing", RequestPairingView),
        (f"{ver}/requestConnectionDetails", RequestConnectionDetailsView),
        (f"{ver}/postConnectionDetails", PostConnectionDetailsView),
        (f"{ver}/finalizePairing", FinalizePairingView),
        (f"{ver}/waitForPairing", WaitForPairingView),
    ]


class PairingApiVersionView(web.View):
    """GET /pairing/ - return supported API major versions."""

    async def get(self) -> web.Response:
        return web.json_response([S2_CONNECT_API_VERSION])


class EndpointView(web.View):
    """GET /pairing/endpoint - return endpoint details (LAN-only, unauthenticated)."""

    async def get(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server
        ep = server.endpoint_description
        if ep is None:
            return web.json_response({"deployment": "LAN"})
        return web.json_response(ep.model_dump(mode="json"))


class NodesView(web.View):
    """GET /pairing/nodes - return list of node descriptions (LAN-only, unauthenticated)."""

    async def get(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server
        # The CEM endpoint represents exactly one node: itself.
        return web.json_response([server.node_description.model_dump(mode="json")])


class PreparePairingView(web.View):
    """POST /pairing/preparePairing"""

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server
        try:
            body = PreparePairingRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info=str(exc))
        if error := server._verify_can_pair(body):
            return error

        # clientNodeId comes from clientNodeDescription.id per spec
        client_node_id = body.clientNodeDescription.id

        if not server.allow_pairing_request(str(client_node_id)):
            return web.Response(status=429)
        # Cap concurrent pending offers; a repeated nodeId replaces its own offer.
        if client_node_id not in server.pairing and len(server.pairing) >= MAX_PENDING_PAIRING_OFFERS:
            return web.Response(status=429)

        # preparePairing only asks us to show the token, so reuse a valid one. Minting a new
        # one here kills the token the user just read from the UI.
        if server.pairing_token is not None and not server.pairing_token.expired():
            token = server.pairing_token
        else:
            token = PairingToken.pairing_token()
        server.pairing[client_node_id] = token

        node = body.clientNodeDescription
        notification_id = f"s2_pebc_pairing_{client_node_id}"
        ctx.host_env.notify(
            notification_id=notification_id,
            title="S2 Resource Manager pairing request",
            message=(
                f"**{_escape_markdown(node.brand)} {_escape_markdown(node.modelName)}** wants to pair.\n\n"
                f"**Pairing token:** `{token.value}`\n\n"
                f"**Valid until:** {token.valid_till.astimezone().strftime('%H:%M:%S')}\n\n"
                f"Enter this token in the Resource Manager UI to complete pairing."
            ),
        )

        # Auto-dismiss notification and clean up token on expiry.
        def _expire() -> None:
            if client_node_id in server.pairing:
                del server.pairing[client_node_id]
            ctx.host_env.dismiss(notification_id=notification_id)

        ctx.scheduler.call_later(PAIRING_TOKEN_TIMEOUT_MINUTES * 60, _expire)

        return web.Response(status=204)


class CancelPreparePairingView(web.View):
    """POST /pairing/cancelPreparePairing - per spec: {clientNodeId, serverNodeId}."""

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server
        try:
            data = await self.request.json()
            client_node_id = UUID(data["clientNodeId"])
        except (ValueError, TypeError, KeyError):
            return web.Response(status=400, reason="Invalid JSON")
        if client_node_id in server.pairing:
            del server.pairing[client_node_id]
        ctx.host_env.dismiss(notification_id=f"s2_pebc_pairing_{client_node_id}")
        return web.Response(status=204)


class RequestPairingView(web.View):
    """POST /pairing/requestPairing

    Token is verified via HMAC challenge-response, never sent directly.
    The clientNodeId is derived from ``clientNodeDescription.id``.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        try:
            body = PairingRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info=str(exc))
        if error := server._verify_can_pair(body):
            return error

        client_node_id = body.clientNodeDescription.id

        # Rate limiting: per-node (spec requires sequential per-node, 1/sec per node)
        if not server.allow_pairing_request(str(client_node_id)):
            return web.Response(status=429)

        # Per-client token (preparePairing / initiate_pairing) wins over the
        # global token (--pairing-token or generate_pairing_info).
        valid_token: str | None = None
        if client_node_id in server.pairing:
            token_obj = server.pairing[client_node_id]
            valid_token = token_obj.value
            del server.pairing[client_node_id]
        elif server.pairing_token is not None and not server.pairing_token.expired():
            valid_token = server.pairing_token.value
        # Token must be available - the HMAC will verify the client knows it
        if valid_token is None:
            return _error(400, PairingErrorMessage.NO_VALID_PAIRING_TOKEN_ON_SERVER)

        # Re-pairing: clean up stale connections
        if client_node_id in server.paired_nodes:
            server.pending_tokens = [p for p in server.pending_tokens if p.node.node_id != client_node_id]
            old_ws = server.active_connections.pop(client_node_id, None)
            if old_ws is not None:
                await old_ws.close()

        access_token = Token.access_token()
        node = _PairedNode(
            access_token,
            body.clientNodeDescription,
            client_node_id,
            endpoint_description=body.clientEndpointDescription,
            certificate_fingerprint=server.certificate_fingerprint,
        )
        server.paired_nodes[client_node_id] = node
        await save_paired_nodes(ctx.persistence, server)
        ctx.host_env.dismiss(notification_id=f"s2_pebc_pairing_{client_node_id}")

        # Compute HMAC challenge-response
        server_challenge = server.generate_hmac_challenge()
        selected_algorithm = HmacHashingAlgorithm.SHA256
        server_domain = self.request.host or ""
        deployment = server.endpoint_description.deployment if server.endpoint_description else Deployment.LAN
        hmac_response = server.compute_hmac_challenge_response(
            body.clientHmacChallenge, valid_token,
            deployment=deployment,
            server_domain=server_domain,
        )

        # Brute-force protection: mandatory 1-second delay (spec requirement)
        await asyncio.sleep(1)

        # Dynamic token is used up by the pairing, static ones (--pairing-token) stay valid.
        if not server._pairing_token_is_fixed:
            server.pairing_token = None

        attempt = server.begin_pairing_attempt(
            client_node_id,
            server_hmac_challenge=server_challenge,
            pairing_token=valid_token,
        )
        response = PairingResponse(
            pairingAttemptId=attempt.attempt_id,
            serverNodeDescription=server.node_description,
            serverEndpointDescription=server.endpoint_description,
            selectedHmacHashingAlgorithm=selected_algorithm,
            clientHmacChallengeResponse=hmac_response,
            serverHmacChallenge=server_challenge,
        )
        return web.Response(status=200, body=response.model_dump_json(), content_type="application/json")


class RequestConnectionDetailsView(web.View):
    """POST /pairing/requestConnectionDetails - verify serverHmacChallengeResponse, return connection info.

    Requires ``Authorization: Bearer <pairingAttemptId>`` header.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        attempt_id = _extract_bearer(self.request)
        if attempt_id is None:
            raise web.HTTPUnauthorized()
        attempt = server.get_pairing_attempt(attempt_id)
        if attempt is None:
            raise web.HTTPUnauthorized()

        try:
            body = ConnectionDetailsRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info=str(exc))

        # Verify the serverHmacChallengeResponse
        deployment = server.endpoint_description.deployment if server.endpoint_description else Deployment.LAN
        if attempt.server_hmac_challenge and attempt.pairing_token and not server.verify_hmac_challenge_response(
                attempt.server_hmac_challenge, body.serverHmacChallengeResponse,
                attempt.pairing_token,
                deployment=deployment,
                server_domain=self.request.host or "",
        ):
            del server.pairing_attempts[attempt_id]
            return _error(403, "HMAC challenge-response verification failed")

        # Generate connection details
        node = server.paired_nodes.get(attempt.client_node_id)
        access_token = Token.access_token()
        if node is not None:
            node.access_token = access_token

        initiate_session_url = f"https://{self.request.host}{S2_CONNECTION_PATH}"
        response = ConnectionDetailsResponse(
            initiateSessionUrl=initiate_session_url,
            accessToken=access_token.value,
        )
        return web.Response(status=200, body=response.model_dump_json(), content_type="application/json")


class PostConnectionDetailsView(web.View):
    """POST /pairing/postConnectionDetails - receive connection details + HMAC response.

    Used when the pairing client will become the communication server.
    Requires ``Authorization: Bearer <pairingAttemptId>`` header.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        attempt_id = _extract_bearer(self.request)
        if attempt_id is None:
            raise web.HTTPUnauthorized()
        attempt = server.get_pairing_attempt(attempt_id)
        if attempt is None:
            raise web.HTTPUnauthorized()

        try:
            body = PostConnectionDetailsRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info=str(exc))

        # Verify the serverHmacChallengeResponse
        deployment = server.endpoint_description.deployment if server.endpoint_description else Deployment.LAN
        if attempt.server_hmac_challenge and attempt.pairing_token and not server.verify_hmac_challenge_response(
            attempt.server_hmac_challenge, body.serverHmacChallengeResponse,
            attempt.pairing_token,
            deployment=deployment,
            server_domain=self.request.host or "",
        ):
            del server.pairing_attempts[attempt_id]
            return _error(403, "HMAC challenge-response verification failed")

        return web.Response(status=204)


class FinalizePairingView(web.View):
    """POST /pairing/finalizePairing - mark the pairing attempt as complete.

    Requires ``Authorization: Bearer <pairingAttemptId>`` header.
    Receiving this request marks the end of the pairing attempt for the server.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        attempt_id = _extract_bearer(self.request)
        if attempt_id is None:
            raise web.HTTPUnauthorized()
        attempt = server.get_pairing_attempt(attempt_id)
        if attempt is None:
            raise web.HTTPUnauthorized()

        try:
            FinalizePairingRequest(**await self.request.json())
        except (ValueError, TypeError):
            return _error(400, PairingErrorMessage.PARSING_ERROR)

        # Delete the pairing attempt - it is now complete.
        if attempt_id in server.pairing_attempts:
            del server.pairing_attempts[attempt_id]

        return web.Response(status=204)


class WaitForPairingView(web.View):
    """POST /pairing/waitForPairing - long-poll for CEM-initiated pairing actions.

    The RM calls this repeatedly. The CEM holds the request for up to 25 seconds.
    If the CEM wants the RM to act, it responds with a list of actions.
    Otherwise it responds 204 (no action, poll again).

    The request must originate from within the LAN subnet.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        try:
            entries = [WaitForPairingEntry(**e) for e in await self.request.json()]
        except (ValueError, TypeError) as exc:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info=str(exc))

        if not entries:
            return _error(400, PairingErrorMessage.PARSING_ERROR, additional_info="Empty request array")

        # Record node/endpoint descriptions and errors reported by the RM.
        events: list[tuple[UUID, asyncio.Event]] = []
        for entry in entries:
            _LOGGER.info("Long-polling RM waiting for pairing: %s", entry.clientNodeId)
            server.longpoll_record_entry(
                entry.clientNodeId,
                node_description=entry.clientNodeDescription,
                endpoint_description=entry.clientEndpointDescription,
                error_message=entry.errorMessage,
            )
            if entry.errorMessage:
                _LOGGER.warning("Long-polling RM %s cannot pair: %s", entry.clientNodeId, entry.errorMessage)
            event = server.longpoll_create_event(entry.clientNodeId)
            events.append((entry.clientNodeId, event))

        try:
            # Wait up to 25 seconds for any event to be triggered
            tasks = [asyncio.create_task(e.wait()) for _, e in events]
            done, _pending = await asyncio.wait(tasks, timeout=25)

            actions = []
            for (node_id, event), task in zip(events, tasks):
                if task in done:
                    action = getattr(event, "_action", None)
                    if action:
                        actions.append(WaitForPairingAction(
                            clientNodeId=node_id, action=action,
                        ).model_dump(mode="json"))

            if actions:
                return web.json_response(actions, status=200)
            return web.Response(status=204)
        finally:
            for node_id, _event in events:
                server.longpoll_remove_event(node_id, _event)
            for task in tasks:
                task.cancel()
