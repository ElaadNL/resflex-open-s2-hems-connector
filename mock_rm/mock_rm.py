# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Mock S2 Resource Manager for testing any S2 CEM implementation.

See mock_rm/README.md for full usage documentation.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import socket
import ssl
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import requests
import urllib3
from s2python.common import (
    Commodity,
    CommodityQuantity,
    Duration,
    InstructionStatus,
    InstructionStatusUpdate,
    NumberRange,
    PowerMeasurement,
    PowerValue,
    RevokableObjects,
    RevokeObject,
    Role,
    RoleType,
)
from s2python.connection.asset_details import AssetDetails
from s2python.connection.async_.medium.websocket import WebsocketClientMedium
from s2python.connection.quickstarts import ResourceManagerHandler
from s2python.connection.sync.connection import S2SyncConnection as S2Connection
from s2python.connection.sync.control_type.class_based import PEBCControlType
from s2python.message import S2Message
from s2python.pebc import (
    PEBCAllowedLimitRange,
    PEBCEnergyConstraint,
    PEBCInstruction,
    PEBCPowerConstraints,
    PEBCPowerEnvelopeConsequenceType,
    PEBCPowerEnvelopeLimitType,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Chance of RM aborting instruction for testing. 1 = 100% chance. 0 = disabled.
ABORT_CHANCE = 0.0

_TERMINAL_STATES = frozenset({
    InstructionStatus.REJECTED,
    InstructionStatus.REVOKED,
    InstructionStatus.SUCCEEDED,
    InstructionStatus.ABORTED,
})

# S2 Connect API version - must match the CEM's S2_CONNECT_API_VERSION
S2_API_VERSION = "v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
logging.disable(logging.DEBUG)  # s2python debug logger crashes on PEBCInstruction serialization
log = logging.getLogger("mock_rm")

VERSIONS = ["0.0.2-beta"]


# ---------------------------------------------------------------------------
# S2 Connect pairing + connection helpers
# ---------------------------------------------------------------------------

def _extract_fingerprint(host: str, port: int, verify: bool = True) -> bytes:
    """Extract SHA-256 fingerprint of the server TLS certificate.

    When verify is True (the default), a full TLS handshake with
    certificate validation is performed and the verified peer certificate
    is fingerprinted.  Set verify to False only for self-signed certs
    on a trusted LAN.
    """
    raw = socket.create_connection((host, port), timeout=10)
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = verify
    if not verify:
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR -- self-signed certs on trusted LAN only
    sock = ctx.wrap_socket(raw, server_hostname=host)
    der = sock.getpeercert(binary_form=True)
    sock.close()
    return hashlib.sha256(der).digest()


def _normalize_cem_url(cem_url: str, api_path: str) -> str:
    """Strip overlapping base from cem_url so paths don't double."""
    if not api_path.startswith("/"):
        return cem_url
    parts = api_path.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        suffix = "/" + "/".join(parts[:i])
        if cem_url.endswith(suffix):
            return cem_url[:-len(suffix)]
    return cem_url


def _fetch_server_node_id(cem_url: str, pairing_path: str, verify: bool) -> str | None:
    """GET /nodes. Per spec the client needs the server node ID to prepare a pairing."""
    try:
        r = requests.get(f"{cem_url}{pairing_path}/{S2_API_VERSION}/nodes",
                         verify=verify, timeout=5)
        if r.status_code == 200:
            return r.json()[0]["id"]
    except requests.RequestException:
        log.debug("Could not fetch server node id")
    return None


def pair(cem_url: str, pairing_path: str, node_id: uuid.UUID, args) -> str:
    """preparePairing -> token from --pairing-token or the prompt -> _pairing_flow.
    Returns the access_token from the connection details response."""
    cem_url = _normalize_cem_url(cem_url, pairing_path)
    base = f"{cem_url}{pairing_path}/{S2_API_VERSION}"
    verify = not args.no_verify_ssl

    node_desc = {
        "id": str(node_id),
        "brand": args.brand,
        "type": "RM",
        "modelName": args.model,
        "role": "RM",
    }
    ep_desc = {"deployment": "LAN"}

    server_node_id = _fetch_server_node_id(cem_url, pairing_path, verify)
    if server_node_id is None:
        log.error("Could not read the server node ID from %s/nodes", base)
        sys.exit(1)

    # 1. preparePairing
    log.info("POST %s/preparePairing", base)
    r = requests.post(
        f"{base}/preparePairing",
        json={
            "clientNodeDescription": node_desc,
            "clientEndpointDescription": ep_desc,
            "serverNodeId": server_node_id,
        },
        verify=verify,
    )
    if r.status_code != 204:
        log.error("preparePairing failed %d: %s", r.status_code, r.text)
        sys.exit(1)

    # --pairing-token means the token is already known, so there is nothing to prompt for. Same token either way.
    token = args.pairing_token
    if token is None:
        log.info("Check your CEM for the pairing token")
        token = input("\n  >>> Paste pairing token: ").strip()
    if not token:
        sys.exit(1)

    return _pairing_flow(cem_url, pairing_path, node_id, token, args, verify)


def _pairing_flow(cem_url: str, pairing_path: str, node_id: uuid.UUID,
                  token: str, args, verify: bool) -> str:
    """requestPairing (HMAC) -> requestConnectionDetails -> finalizePairing.

    Uses the provided pairing token (no prompting). Returns the access token."""
    cem_url = _normalize_cem_url(cem_url, pairing_path)
    base = f"{cem_url}{pairing_path}/{S2_API_VERSION}"

    host = urllib3.util.parse_url(cem_url).host or "localhost"
    port = urllib3.util.parse_url(cem_url).port or (443 if cem_url.startswith("https") else 80)
    fingerprint = _extract_fingerprint(host, port, verify=verify)
    log.info("Server certificate fingerprint: %s", fingerprint.hex())

    node_desc = {
        "id": str(node_id),
        "brand": args.brand,
        "type": "RM",
        "modelName": args.model,
        "role": "RM",
    }
    ep_desc = {"deployment": "LAN"}

    # requestPairing (with HMAC challenge-response)
    client_challenge = base64.b64encode(secrets.token_bytes(32)).decode()
    log.info("POST %s/requestPairing", base)
    r = requests.post(
        f"{base}/requestPairing",
        json={
            "clientNodeDescription": node_desc,
            "clientEndpointDescription": ep_desc,
            "supportedCommunicationProtocols": ["WebSocket"],
            "supportedS2MessageVersions": VERSIONS,
            "supportedHmacHashingAlgorithms": ["SHA256"],
            "clientHmacChallenge": client_challenge,
        },
        verify=verify,
    )
    if r.status_code != 200:
        log.error("requestPairing failed %d: %s", r.status_code, r.text)
        sys.exit(1)
    data = r.json()
    attempt_id = data["pairingAttemptId"]
    server_challenge = data["serverHmacChallenge"]

    # Verify server HMAC response
    expected = hmac.digest(base64.b64decode(client_challenge), token.encode() + fingerprint, hashlib.sha256)
    actual = base64.b64decode(data["clientHmacChallengeResponse"])
    if not hmac.compare_digest(expected, actual):
        log.error("Server HMAC response mismatch - wrong token or fingerprint")
        sys.exit(1)
    log.info("HMAC challenge-response verified OK")

    # requestConnectionDetails
    server_hmac_resp = hmac.digest(
        base64.b64decode(server_challenge), token.encode() + fingerprint, hashlib.sha256
    )
    log.info("POST %s/requestConnectionDetails", base)
    r = requests.post(
        f"{base}/requestConnectionDetails",
        json={"serverHmacChallengeResponse": base64.b64encode(server_hmac_resp).decode()},
        headers={"Authorization": f"Bearer {attempt_id}"},
        verify=verify,
    )
    if r.status_code != 200:
        log.error("requestConnectionDetails failed %d: %s", r.status_code, r.text)
        sys.exit(1)
    conn_data = r.json()
    log.info("Connection details received")

    # finalizePairing
    log.info("POST %s/finalizePairing", base)
    r = requests.post(
        f"{base}/finalizePairing",
        json={"success": True},
        headers={"Authorization": f"Bearer {attempt_id}"},
        verify=verify,
    )
    if r.status_code != 204:
        log.error("finalizePairing failed %d: %s", r.status_code, r.text)
        sys.exit(1)
    log.info("Pairing complete - accessToken=%s", conn_data["accessToken"][:8] + "...")
    return conn_data["accessToken"]


def long_poll_pair(cem_url: str, pairing_path: str, node_id: uuid.UUID, args) -> str:
    """Long-polling pairing (CEM is the initiator, RM has no HTTP server).

    Polls /waitForPairing; on requestPairing, run _pairing_flow with --pairing-token.
    """
    cem_url = _normalize_cem_url(cem_url, pairing_path)
    base = f"{cem_url}{pairing_path}/{S2_API_VERSION}"
    verify = not args.no_verify_ssl

    if not args.pairing_token:
        log.error("--long-poll requires --pairing-token (the RM's static pairing token)")
        sys.exit(1)

    node_desc = {
        "id": str(node_id),
        "brand": args.brand,
        "type": "RM",
        "modelName": args.model,
        "role": "RM",
    }
    ep_desc = {"deployment": "LAN"}

    include_descriptions = False
    while True:
        body = [{"clientNodeId": str(node_id)}]
        if include_descriptions:
            body[0]["clientNodeDescription"] = node_desc
            body[0]["clientEndpointDescription"] = ep_desc
            include_descriptions = False

        log.info("POST %s/waitForPairing", base)
        try:
            r = requests.post(f"{base}/waitForPairing", json=body, verify=verify, timeout=30)
        except requests.RequestException as e:
            log.warning("waitForPairing failed (%s), retrying in 1s...", e)
            time.sleep(1)
            continue

        should_pair, include_descriptions = _handle_long_poll_response(r)
        if should_pair:
            return _pairing_flow(cem_url, pairing_path, node_id, args.pairing_token, args, verify)


def _handle_long_poll_response(r) -> tuple[bool, bool]:
    """Handle a waitForPairing response. Returns (should_pair, include_descriptions)."""
    if r.status_code == 204:
        return False, False
    if r.status_code == 200:
        return _handle_long_poll_actions(r.json())
    if r.status_code in (400, 401):
        log.error("waitForPairing rejected (%d): %s - stopping", r.status_code, r.text)
        sys.exit(1)
    log.warning("waitForPairing status %d, retrying in 1s...", r.status_code)
    time.sleep(1)
    return False, False


def _handle_long_poll_actions(actions) -> tuple[bool, bool]:
    """Process waitForPairing actions. Returns (should_pair, include_descriptions)."""
    include_descriptions = False
    for action in actions:
        act = action.get("action")
        log.info("waitForPairing action: %s", act)
        if act == "sendNodeDescription":
            include_descriptions = True
        elif act == "preparePairing":
            log.info("CEM requests the pairing code (show it in the RM UI)")
        elif act == "cancelPreparePairing":
            log.info("CEM cancelled the pairing code (hide it in the RM UI)")
        elif act == "requestPairing":
            log.info("CEM initiated pairing")
            return True, False
    return False, include_descriptions


def initiate_connection(
    cem_url: str,
    connection_path: str,
    pairing_path: str,
    node_id: uuid.UUID,
    access_token: str,
    verify: bool = True,
) -> tuple[str, str, str]:
    """POST /connection/v1/initiateSession -> /confirmAccessToken.
    Returns (comm_token, new_access_token, ws_url)."""
    cem_url = _normalize_cem_url(cem_url, connection_path)
    base = f"{cem_url}{connection_path}/{S2_API_VERSION}"

    # Get server node ID (best-effort; falls back to placeholder)
    server_node_id = "00000000-0000-0000-0000-000000000000"
    try:
        nodes_url = f"{cem_url}{pairing_path}/{S2_API_VERSION}/nodes"
        r = requests.get(nodes_url, verify=verify, timeout=5)
        if r.status_code == 200:
            server_node_id = r.json()[0]["id"]
    except requests.RequestException:
        log.debug("Could not fetch server node id, using placeholder")

    # 1. initiateSession
    log.info("POST %s/initiateSession", base)
    r = requests.post(
        f"{base}/initiateSession",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "clientNodeId": str(node_id),
            "serverNodeId": server_node_id,
            "supportedS2MessageVersions": VERSIONS,
            "supportedCommunicationProtocols": ["WebSocket"],
        },
        verify=verify,
    )
    if r.status_code != 200:
        log.error("initiateSession failed %d: %s", r.status_code, r.text)
        raise requests.HTTPError(response=r)
    data = r.json()
    pending_token = data["accessToken"]
    log.info("Session initiated - pendingToken=%s", pending_token[:8] + "...")

    # 2. confirmAccessToken
    log.info("POST %s/confirmAccessToken", base)
    r = requests.post(
        f"{base}/confirmAccessToken",
        headers={"Authorization": f"Bearer {pending_token}"},
        verify=verify,
    )
    if r.status_code != 200:
        log.error("confirmAccessToken failed %d: %s", r.status_code, r.text)
        raise requests.HTTPError(response=r)
    ws_data = r.json()
    log.info("WebSocket URL: %s", ws_data["websocketUrl"])
    log.info("WebSocket token: %s", ws_data["websocketToken"][:16] + "...")
    return ws_data["websocketToken"], pending_token, ws_data["websocketUrl"]


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------
def _add_normal_ranges(ranges, quantities, min_power, max_power):
    """Add non-abnormal upper/lower limit ranges for each quantity."""
    for q in quantities:
        ranges.append(PEBCAllowedLimitRange(
            commodity_quantity=q,
            limit_type=PEBCPowerEnvelopeLimitType.UPPER_LIMIT,
            range_boundary=NumberRange(start_of_range=0.0, end_of_range=max_power),
            abnormal_condition_only=False,
        ))
        ranges.append(PEBCAllowedLimitRange(
            commodity_quantity=q,
            limit_type=PEBCPowerEnvelopeLimitType.LOWER_LIMIT,
            range_boundary=NumberRange(start_of_range=min_power, end_of_range=max_power),
            abnormal_condition_only=False,
        ))


def _add_abnormal_ranges(ranges, quantities, max_power):
    """Add abnormal-condition upper limit ranges (20% over max)."""
    for q in quantities:
        ranges.append(PEBCAllowedLimitRange(
            commodity_quantity=q,
            limit_type=PEBCPowerEnvelopeLimitType.UPPER_LIMIT,
            range_boundary=NumberRange(start_of_range=0.0, end_of_range=max_power * 1.2),
            abnormal_condition_only=True,
        ))


def make_power_constraints(min_power: float, max_power: float, quantities: list, abnormal: bool = False) -> PEBCPowerConstraints:
    ranges: list[PEBCAllowedLimitRange] = []
    _add_normal_ranges(ranges, quantities, min_power, max_power)
    if abnormal:
        _add_abnormal_ranges(ranges, quantities, max_power)

    return PEBCPowerConstraints(
        message_id=uuid.uuid4(),
        id=uuid.uuid4(),
        valid_from=datetime.now(timezone.utc),
        valid_until=None,
        consequence_type=PEBCPowerEnvelopeConsequenceType.VANISH,
        allowed_limit_ranges=ranges,
    )


def make_energy_constraints(max_power: float, quantities: list) -> list[PEBCEnergyConstraint]:
    """Create energy constraints (24-hour average power budget) per commodity quantity."""
    valid_from = datetime.now(timezone.utc)
    valid_until = valid_from + timedelta(hours=24)
    return [
        PEBCEnergyConstraint(
            message_id=uuid.uuid4(),
            id=uuid.uuid4(),
            valid_from=valid_from,
            valid_until=valid_until,
            upper_average_power=max_power,
            lower_average_power=0.0,
            commodity_quantity=q,
        )
        for q in quantities
    ]


def _extract_duration_ms(el) -> int | None:
    """Try to extract milliseconds from a duration value in any common format."""
    d = getattr(el, "duration", None)
    if d is None and isinstance(el, dict):
        d = el.get("duration")
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return int(d)
    if hasattr(d, "total_seconds"):
        try:
            return int(d.total_seconds() * 1000)
        except (AttributeError, ValueError, OverflowError):
            log.debug("total_seconds() failed for duration object, trying fallback attrs")
    return _duration_attr_fallback(d)


def _duration_attr_fallback(d) -> int | None:
    """Try common attribute names and to_milliseconds()."""
    for attr in ("milliseconds", "millis", "ms", "value"):
        if hasattr(d, attr):
            try:
                return int(getattr(d, attr))
            except (AttributeError, ValueError, TypeError):
                log.debug("getattr(%s) failed for duration object", attr)
    if hasattr(d, "to_milliseconds"):
        try:
            return int(d.to_milliseconds())
        except (AttributeError, ValueError, OverflowError):
            log.debug("to_milliseconds() failed for duration object")
    return None


def make_power_measurement(
    quantities: list[str],
    active_limits: dict[str, tuple[float, float]] | None = None,
    current_power: float = 0.0,
) -> PowerMeasurement:
    def _value_for(q: str) -> float:
        # All phases track the shared current_power with +-50 W per-phase noise
        # to simulate a balanced 3-phase inverter with minor imbalances.
        noise = random.uniform(-50.0, 50.0)
        value = current_power + noise
        if active_limits and q in active_limits:
            lo, hi = active_limits[q]
            return round(max(lo, min(hi, value)), 1)
        return round(value, 1)

    return PowerMeasurement(
        message_id=uuid.uuid4(),
        measurement_timestamp=datetime.now(timezone.utc),
        values=[
            PowerValue(
                commodity_quantity=CommodityQuantity(q),
                value=_value_for(q),
            )
            for q in quantities
        ],
    )


# ---------------------------------------------------------------------------
# Control type implementations
#
# Each control type class handles a specific S2 interaction pattern.
# To add support for a new pattern (e.g. FRBC), create a new class
# extending the appropriate base and add an instance to the
# ``control_types`` list in main().
# ---------------------------------------------------------------------------

class MockPEBCControlType(PEBCControlType):
    def __init__(self, args) -> None:
        self._args = args
        self._min_power: float = args.min_power
        self._max_power: float = args.max_power
        self._log_interval: int = args.log_interval
        self._log_batch_size: int = args.log_batch_size
        self._log_buffer: list[dict] = []
        self._quantities = [CommodityQuantity(q) for q in args.quantities]
        self._is_symmetric = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC in self._quantities
        # track active limits received from CEM in the last instruction
        self._active_limits: dict[str, tuple[float, float]] | None = None
        # default limits advertised by this RM (populated at activate)
        self._default_active_limits: dict[str, tuple[float, float]] | None = None
        self._stop_event = threading.Event()
        self._current_power: float = 0.0
        self._measure_thread: threading.Thread | None = None

    def _send_status_update(self, conn, instruction_id, status_type):
        """Send an InstructionStatusUpdate, track state, and log it."""
        if not hasattr(self, "_instruction_states"):
            self._instruction_states: dict[uuid.UUID, InstructionStatus] = {}

        current = self._instruction_states.get(instruction_id)
        if current is not None and current in _TERMINAL_STATES:
            log.warning(" -> InstructionStatusUpdate SKIPPED (%s): instruction %s already in terminal state %s",
                        status_type.name, str(instruction_id)[:8], current.name)
            return

        self._instruction_states[instruction_id] = status_type
        conn.send_msg_and_await_reception_status(
            InstructionStatusUpdate(
                message_id=uuid.uuid4(),
                instruction_id=instruction_id,
                status_type=status_type,
                timestamp=datetime.now(timezone.utc),
            ),
            raise_on_error=False,
        )
        log.info(" -> InstructionStatusUpdate  %s", status_type.name)

    def register_handlers(self, conn: S2Connection) -> None:
        super().register_handlers(conn)
        conn.register_handler(RevokeObject, self._handle_revoke_object)

    def activate(self, conn: S2Connection) -> None:
        mode = "3-phase-symmetric" if self._is_symmetric else "per-phase"
        log.info("PEBC activated - sending PowerConstraints min=%.0fW max=%.0fW mode=%s",
                 self._min_power, self._max_power, mode)
        pc = make_power_constraints(self._min_power, self._max_power,
                                    quantities=self._quantities,
                                    abnormal=self._args.abnormal_ranges)
        conn.send_msg_and_await_reception_status(pc, raise_on_error=False)
        self._default_active_limits = self._build_default_limits(pc) or None
        self._active_limits = dict(self._default_active_limits) if self._default_active_limits else None
        log.info("PEBC activated - sending EnergyConstraints max=%.0fW mode=%s", self._max_power, mode)
        for ec in make_energy_constraints(self._max_power, quantities=self._quantities):
            conn.send_msg_and_await_reception_status(ec, raise_on_error=False)
        self._stop_event.clear()
        self._measure_thread = threading.Thread(
            target=self._measure_loop, args=(conn,), daemon=True
        )
        self._measure_thread.start()

    def deactivate(self, conn: S2Connection) -> None:
        self._stop_event.set()
        if self._measure_thread:
            self._measure_thread.join()

    def handle_instruction(
        self, conn: S2Connection, msg: S2Message, send_okay: Callable[[], None]
    ) -> None:
        if not isinstance(msg, PEBCInstruction):
            log.info("INSTRUCTION: \n" + msg.to_json_dict(indent=2))
            send_okay()
            return
        log.info("<- PEBC.Instruction  id=%s\n%s", str(msg.id)[:8], json.dumps(json.loads(msg.to_json()), indent=2))
        send_okay()
        self._process_instruction_status_updates(conn=conn, instruction=msg)

    def _process_instruction_status_updates(self, conn: S2Connection, instruction: PEBCInstruction) -> None:    
        self._active_limits = dict(self._default_active_limits) if self._default_active_limits else None

        self._send_status_update(conn, instruction.id, InstructionStatus.NEW)

        if not self._is_within_constraints(instruction):
            self._send_status_update(conn, instruction.id, InstructionStatus.REJECTED)
            return

        self._send_status_update(conn, instruction.id, InstructionStatus.ACCEPTED)

        if random.random() < ABORT_CHANCE:
            self._send_status_update(conn, instruction.id, InstructionStatus.ABORTED)
            return

        start, duration_ms = self._parse_instruction_timing(instruction)
        self._start_worker(conn, instruction, start, duration_ms)

    def _start_worker(self, conn, instruction, start, duration_ms):
        """Spawn a background thread that sends STARTED and eventually SUCCEEDED."""
        if not hasattr(self, "_instruction_lock"):
            self._instruction_lock = threading.Lock()
        if not hasattr(self, "_active_instruction"):
            self._active_instruction = None

        cancel_event = threading.Event()
        start_ts = start.timestamp() if isinstance(start, datetime) else time.time()
        end_dt = start + timedelta(milliseconds=duration_ms)

        worker = threading.Thread(
            target=self._run_instruction_worker,
            args=(conn, instruction, start, duration_ms, cancel_event),
            daemon=True,
        )

        with self._instruction_lock:
            existing = getattr(self, "_active_instruction", None)
            if existing and start_ts >= existing.get("start_ts", 0):
                existing.get("cancel_event").set()
                log.info(" -> Overriding instruction %s with %s",
                         str(existing.get("id"))[:8], str(instruction.id)[:8])
            self._active_instruction = {
                "id": instruction.id, "start_ts": start_ts,
                "end_dt": end_dt, "cancel_event": cancel_event,
            }
        worker.start()

    def _run_instruction_worker(self, conn, instr, start_dt, dur_ms, cancel_event):
        """Background thread: wait until start, send STARTED, wait until end, send SUCCEEDED.

        Respects cancellation:
        - If cancelled before STARTED: the instruction was overridden or revoked; just exit silently.
        - If cancelled after STARTED: send ABORTED (overridden by newer instruction) or
          REVOKED was already sent by _handle_revoke_object; the state guard in
          _send_status_update prevents duplicate terminal states.
        """
        self._sleep_until(start_dt, cancel_event)
        if cancel_event.is_set():
            return

        with self._instruction_lock:
            if not self._active_instruction or self._active_instruction.get("id") != instr.id:
                return
            self._set_active_limits(instr)
        self._send_status_update(conn, instr.id, InstructionStatus.STARTED)

        end_dt = start_dt + timedelta(milliseconds=dur_ms)
        self._sleep_until(end_dt, cancel_event)
        if cancel_event.is_set():
            if not hasattr(self, "_instruction_states"):
                self._instruction_states: dict[uuid.UUID, InstructionStatus] = {}
            current = self._instruction_states.get(instr.id)
            if current != InstructionStatus.REVOKED:
                self._send_status_update(conn, instr.id, InstructionStatus.ABORTED)
            self._clear_active_instruction(instr)
            return

        self._send_status_update(conn, instr.id, InstructionStatus.SUCCEEDED)
        self._clear_active_instruction(instr)

    @staticmethod
    def _sleep_until(target_dt, cancel_event):
        """Sleep until target_dt or cancelled."""
        while not cancel_event.is_set():
            remaining = (target_dt - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                return
            time.sleep(min(0.5, remaining))

    def _set_active_limits(self, instr):
        """Set active limits from the instruction's power envelopes."""
        limits = {}
        for env in getattr(instr, "power_envelopes", []):
            elements = getattr(env, "power_envelope_elements", None)
            if not elements:
                continue
            limits[env.commodity_quantity.value] = (
                elements[0].lower_limit,
                elements[0].upper_limit,
            )
        self._active_limits = limits

    def _clear_active_instruction(self, instr):
        """Reset active limits to defaults if instr is still the active one."""
        self._clear_active_instruction_by_id(instr.id)

    def _clear_active_instruction_by_id(self, instr_id: uuid.UUID) -> None:
        """Clear active instruction by ID, resetting limits."""
        if not hasattr(self, "_instruction_lock"):
            return
        with self._instruction_lock:
            if self._active_instruction and self._active_instruction.get("id") == instr_id:
                self._active_limits = dict(self._default_active_limits) if self._default_active_limits else None
                self._active_instruction = None


    def _handle_revoke_object(
        self, conn: S2Connection, msg: S2Message, send_okay: Callable[[], None]
    ) -> None:
        if not isinstance(msg, RevokeObject) or msg.object_type != RevokableObjects.PEBC_Instruction:
            send_okay()
            return
        send_okay()

        instruction_id = msg.object_id
        if not hasattr(self, "_instruction_states"):
            self._instruction_states: dict[uuid.UUID, InstructionStatus] = {}

        state = self._instruction_states.get(instruction_id)
        if state is None:
            log.info("REVOKE for unknown instruction %s — acknowledged", str(instruction_id)[:8])
            return
        if state in _TERMINAL_STATES:
            log.info("REVOKE for instruction %s already in terminal state %s — ignored",
                     str(instruction_id)[:8], state.name)
            return

        # Cancel the active worker if this is the currently active instruction.
        if hasattr(self, "_instruction_lock"):
            with self._instruction_lock:
                active = getattr(self, "_active_instruction", None)
                if active and active.get("id") == instruction_id:
                    active.get("cancel_event", threading.Event()).set()
                    log.info(" -> Cancelled active worker for instruction %s", str(instruction_id)[:8])

        # Restore default active limits.
        self._active_limits = dict(self._default_active_limits) if self._default_active_limits else None
        self._clear_active_instruction_by_id(instruction_id)
        self._send_status_update(conn, instruction_id, InstructionStatus.REVOKED)

    def _parse_instruction_timing(self, instruction: PEBCInstruction) -> tuple[datetime, int]:
        """Extract start time and max duration (ms) from an instruction."""
        start = self._parse_start_time(instruction)
        duration_ms = self._parse_max_duration(instruction)
        try:
            end = start + timedelta(milliseconds=duration_ms)
            log.info("Instruction timing: start=%s duration_ms=%d end=%s",
                     start.isoformat(), duration_ms, end.isoformat())
        except (AttributeError, OverflowError, OSError):
            log.info("Instruction timing: start=%s duration_ms=%d", start, duration_ms)
        return start, duration_ms

    @staticmethod
    def _parse_start_time(instruction) -> datetime:
        """Parse execution_time from an instruction, falling back to now."""
        start = getattr(instruction, "execution_time", None) or datetime.now(timezone.utc)
        if isinstance(start, str):
            try:
                start = datetime.fromisoformat(start.rstrip("Z"))
            except ValueError:
                start = datetime.now(timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return start

    @staticmethod
    def _parse_max_duration(instruction) -> int:
        """Find the max duration (ms) across all power envelope elements."""
        duration_ms = 0
        for env in getattr(instruction, "power_envelopes", []):
            for el in getattr(env, "power_envelope_elements", []):
                ms = _extract_duration_ms(el)
                if ms is not None:
                    duration_ms = max(duration_ms, ms)
        # Fallback: try JSON parsing for bare numeric durations
        try:
            j = json.loads(instruction.to_json())
            for env in j.get("power_envelopes", []):
                for el in env.get("power_envelope_elements", []):
                    d = el.get("duration")
                    if isinstance(d, (int, float)):
                        duration_ms = max(duration_ms, int(d))
        except (ValueError, AttributeError):
            log.debug("JSON fallback failed for instruction duration parsing")
        return duration_ms

    def _build_default_limits(self, pc: PEBCPowerConstraints) -> dict[str, tuple[float, float]]:
        """Build {commodity_quantity: (lower, upper)} from advertised constraints."""
        limits: dict[str, tuple[float, float]] = {}
        for r in pc.allowed_limit_ranges:
            try:
                if getattr(r, "abnormal_condition_only", False):
                    continue
                cq = r.commodity_quantity.value
                lo, hi = limits.get(cq, (None, None))
                if r.limit_type == PEBCPowerEnvelopeLimitType.UPPER_LIMIT:
                    hi = getattr(r.range_boundary, "end_of_range", None)
                    lo = lo if lo is not None else 0.0
                elif r.limit_type == PEBCPowerEnvelopeLimitType.LOWER_LIMIT:
                    lo = getattr(r.range_boundary, "start_of_range", None)
                    hi = hi if hi is not None else self._max_power
                limits[cq] = (lo, hi)
            except (AttributeError, ValueError, TypeError):
                continue
        return limits

    def _is_within_constraints(self, instruction: PEBCInstruction) -> bool:
        advertised = set(self._quantities)
        multiplier = 1.2 if instruction.abnormal_condition else 1.0
        for envelope in instruction.power_envelopes:
            if envelope.commodity_quantity not in advertised:
                return False
            limit_hi = self._max_power * multiplier
            limit_lo = self._min_power * multiplier
            for element in envelope.power_envelope_elements:
                if element.upper_limit > limit_hi:
                    return False
                if element.lower_limit < limit_lo:
                    return False
        return True

    def _measure_loop(self, conn: S2Connection) -> None:
        time.sleep(1)
        tick = 0
        while not self._stop_event.is_set():
            # Determine the allowed range for the random walk
            if self._active_limits:
                first_q = next(iter(self._active_limits))
                lo, hi = self._active_limits[first_q]
            else:
                lo, hi = self._min_power, self._max_power
            # Random walk: ramp at up to 500 W per tick, clamped to [lo, hi]
            self._current_power += random.uniform(-500.0, 500.0)
            self._current_power = max(lo, min(hi, self._current_power))
            pm = make_power_measurement(self._args.quantities, self._active_limits, self._current_power)
            summary = ", ".join(
                f"{v.commodity_quantity.value.split('.')[-1]}={v.value}W"
                for v in pm.values
            )
            log.info("  -> PowerMeasurement [%d]  %s", tick, summary)
            conn.send_msg_and_await_reception_status(pm, raise_on_error=False)
            if self._log_interval > 0 and tick % self._log_interval == 0:
                self._send_device_log(conn, tick)
            tick += 1
            self._stop_event.wait(self._args.interval)

    def _send_device_log(self, conn, tick: int) -> None:
        """Send an S2 log or log_batch frame as raw JSON through the WebSocket."""
        levels = ["INFO", "INFO", "INFO", "WARN"]  # occasional WARN for testing
        entry = {
            "level": levels[tick % len(levels)],
            "message": f"Mock RM tick {tick}: power={self._current_power:.0f}W, limits={self._active_limits}",
        }
        if self._log_batch_size > 0:
            self._log_buffer.append(entry)
            if len(self._log_buffer) >= self._log_batch_size:
                batch = {
                    "type": "log_batch",
                    "batch_id": str(uuid.uuid4()),
                    "messages": self._log_buffer,
                }
                self._log_buffer = []
                self._send_raw_log(conn, batch, tick, "batch")
        else:
            log_msg = {"type": "log", "id": str(uuid.uuid4()), **entry}
            self._send_raw_log(conn, log_msg, tick, entry["level"])

    def _send_raw_log(self, conn, payload: dict, tick: int, label: str) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                conn._async_s2_connection._medium.send(json.dumps(payload)),
                conn._eventloop,
            ).result(timeout=5)
            log.info("  -> DeviceLog [%d]  %s", tick, label)
        except (asyncio.TimeoutError, RuntimeError, ConnectionError) as e:
            log.warning("  -> DeviceLog FAILED: %s", e)


# ---------------------------------------------------------------------------

def main() -> None:
    random_model_number = str(random.randint(0, 9999)).zfill(4)
    parser = argparse.ArgumentParser(description="Mock S2 Resource Manager")
    parser.add_argument("--cem-url", default=None, help="CEM base URL (prompted if not set)")
    parser.add_argument("--pairing-path", default="/api/s2_pebc/pairing", help="Pairing API base path")
    parser.add_argument("--connection-path", default="/api/s2_pebc/connection", help="Connection API base path")
    parser.add_argument("--name", default="Mock RM")
    parser.add_argument("--brand", default="MockCo")
    parser.add_argument("--model", default=f"MockRM-{random_model_number}")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between measurements")
    parser.add_argument("--power", type=float, default=5000.0, help="Base power in Watts (sets both min and max if not specified)")
    parser.add_argument("--min-power", type=float, default=None, help="Minimum power in Watts (negative = production). Defaults to --power.")
    parser.add_argument("--max-power", type=float, default=None, help="Maximum power in Watts (positive = consumption). Defaults to --power.")
    parser.add_argument("--quantities", nargs="+", default=["ELECTRIC.POWER.L1", "ELECTRIC.POWER.L2", "ELECTRIC.POWER.L3"],
                        help="Commodity quantities to advertise. "
                             "Use ELECTRIC.POWER.3_PHASE_SYMMETRIC for 3-phase symmetric, "
                             "or L1/L2/L3 for per-phase modes.")
    parser.add_argument("--abnormal-ranges", action="store_true", default=False,
                        help="Include abnormal condition ranges (20% over max power).")
    parser.add_argument("--node-id", default=None)
    parser.add_argument("--access-token", default=None)
    parser.add_argument("--no-verify-ssl", action="store_true", default=False,
                        help="Disable SSL certificate verification")
    parser.add_argument("--state-file", default="mock_rm_state.json",
                        help="File to persist node-id and access-token between runs")
    parser.add_argument("--log-interval", type=int, default=0, metavar="TICKS",
                        help="Send a device log frame every N measurement ticks (0 = disabled)")
    parser.add_argument("--log-batch-size", type=int, default=0, metavar="N",
                        help="Batch log frames in groups of N before sending as log_batch (0 = send individually)")
    parser.add_argument("--long-poll", action="store_true", default=False,
                        help="Use long-polling pairing (CEM is the initiator, RM has no HTTP server)")
    parser.add_argument("--pairing-token", default=None,
                        help="Static pairing token for --long-poll (the RM's own code)")
    args = parser.parse_args()

    _prompt_cem_url(args)
    _load_state(args)

    if args.min_power is None:
        args.min_power = -args.power
    if args.max_power is None:
        args.max_power = args.power
    if args.min_power > args.max_power:
        log.error("min-power (%.0f) must be <= max-power (%.0f)", args.min_power, args.max_power)
        sys.exit(1)

    node_id = uuid.UUID(args.node_id) if args.node_id else uuid.uuid4()
    log.info("CEM: %s  node_id=%s  name=%s", args.cem_url, node_id, args.name)

    _setup_ssl(args)

    verify = not args.no_verify_ssl
    if args.long_poll:
        access_token = args.access_token or long_poll_pair(args.cem_url, args.pairing_path, node_id, args)
    else:
        access_token = args.access_token or pair(args.cem_url, args.pairing_path, node_id, args)

    asset = AssetDetails(
        resource_id=node_id,
        provides_forecast=False,
        provides_power_measurements=[CommodityQuantity(q) for q in args.quantities],
        instruction_processing_delay=Duration.from_milliseconds(100),
        roles=[Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
        name=args.name,
        manufacturer=args.brand,
        model=args.model,
        serial_number="MOCK-" + str(uuid.uuid4()),
        firmware_version="1.0.0",
    )

    _run_reconnect_loop(args, node_id, access_token, asset, verify)


# ---------------------------------------------------------------------------
# Main helpers
# ---------------------------------------------------------------------------

def _prompt_cem_url(args):
    if not args.cem_url:
        entered = input("  >>> CEM URL: ").strip()
        if entered:
            args.cem_url = entered


def _load_state(args):
    state_path = os.path.join(os.path.dirname(__file__), "state", args.state_file)
    if not (args.state_file and os.path.exists(state_path)
            and not args.node_id and not args.access_token):
        return
    try:
        with open(state_path) as f:
            state = json.load(f)
        args.node_id = state.get("node_id")
        args.access_token = state.get("access_token")
        log.info("Loaded state from %s (node_id=%s)", state_path, args.node_id)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not load state file %s: %s", state_path, e)


def _setup_ssl(args):
    if args.no_verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("SSL certificate verification disabled")


def _run_reconnect_loop(args, node_id, access_token, asset, verify):
    reconnect_delay = 5
    while True:
        try:
            comm_token, new_access_token, ws_url = initiate_connection(
                args.cem_url, args.connection_path, args.pairing_path,
                node_id, access_token, verify=verify)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                log.warning("Access token rejected (401) - re-pairing required")
                if args.long_poll:
                    access_token = long_poll_pair(args.cem_url, args.pairing_path, node_id, args)
                else:
                    access_token = pair(args.cem_url, args.pairing_path, node_id, args)
                continue
            log.error("Connection failed, retrying in %ds...", reconnect_delay)
            time.sleep(reconnect_delay)
            continue
        except requests.RequestException as e:
            log.error("Connection failed (%s), retrying in %ds...", e, reconnect_delay)
            time.sleep(reconnect_delay)
            continue

        access_token = new_access_token
        _save_state(args, node_id, new_access_token)

        control_types = [MockPEBCControlType(args)]
        rm_handler = ResourceManagerHandler(control_types=control_types, asset_details=asset)

        eventloop = asyncio.new_event_loop()
        ws_medium = WebsocketClientMedium(url=ws_url, verify_certificate=False, bearer_token=comm_token)
        eventloop.run_until_complete(ws_medium.connect())
        conn = S2Connection(medium=ws_medium, eventloop=eventloop)
        rm_handler.register_handlers(conn)

        log.info("Connecting to %s", ws_url)
        conn.run()
        eventloop.close()
        log.info("Disconnected. Reconnecting in %ds...", reconnect_delay)
        time.sleep(reconnect_delay)


def _save_state(args, node_id, access_token):
    if not args.state_file:
        return
    state_dir = os.path.join(os.path.dirname(__file__), "state")
    state_path = os.path.join(state_dir, args.state_file)
    os.makedirs(state_dir, exist_ok=True)
    try:
        with open(state_path, "w") as f:
            json.dump({"node_id": str(node_id), "access_token": access_token}, f)
    except OSError as e:
        log.warning("Could not save state file %s: %s", state_path, e)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
