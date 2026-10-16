# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Standalone runner for the S2 CEM engine

Usage:
    python run.py [--host 0.0.0.0] [--port 8443] [--tls-cert cert.pem] [--tls-key key.pem]

S2 Connect requires TLS.  If no cert/key are given a self-signed
certificate is generated on startup (for LAN it's good enough).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import logging
import os
import ssl
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

from s2_cem_lib import EventBus, HostEnv, Persistence, S2CemApp, TaskScheduler
from s2_cem_lib.dns_sd import S2DnsSdAdvertiser
from s2_cem_lib.pairing.pairing_models import (
    CommunicationProtocol,
    Deployment,
    EndpointDescription,
    Role,
    S2NodeDescription,
)
from s2_cem_lib.websocket.node_state import get_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("s2python").setLevel(logging.WARNING)
_LOGGER = logging.getLogger("run")

_INVALID_JSON_BODY = "invalid JSON body"
_PAIRING_SERVER_NOT_INIT = "pairing server not init"

# paired nodes, access tokens and fingerprints, so a restart accepts reconnecting RMs
_STATE_FILE = Path(__file__).parent / "state" / "paired_nodes.json"


# ---------------------------------------------------------------------------
# In-memory port stubs
# ---------------------------------------------------------------------------

class _FilePersistence(Persistence):
    """Stores paired nodes in a JSON file so a restart keeps them."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> dict | None:
        if not self._path.exists():
            return None
        return json.loads(self._path.read_text())

    async def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    async def remove(self) -> None:
        self._path.unlink(missing_ok=True)


class _PrintEventBus(EventBus):
    """Logs events to stdout instead of a real event bus."""

    def send(self, signal: str, *args: Any) -> None:
        _LOGGER.info("event  %s  %s", signal, args[:2] if args else "")


class _ConsoleHostEnv(HostEnv):
    """Prints notifications to the console."""

    def notify(self, notification_id: str, title: str, message: str) -> None:
        _LOGGER.info("NOTIFY [%s]  %s: %s", notification_id, title, message)

    def dismiss(self, notification_id: str) -> None:
        _LOGGER.info("DISMISS  %s", notification_id)


class _AsyncScheduler(TaskScheduler):
    """Runs tasks on the asyncio event loop."""

    def create_task(self, coro) -> None:
        _ = asyncio.create_task(coro)

    def call_later(self, delay_seconds: float, cb: Callable[[], None]) -> Callable[[], None]:
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_seconds, cb)
        return lambda: handle.cancel()


def _generate_self_signed_cert() -> tuple[str, str]:
    """Generate a self-signed cert + key, return (cert_path, key_path)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "s2-cem-standalone")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    tmp = Path(tempfile.mkdtemp(prefix="s2-cem-"))
    cert_path = tmp / "cert.pem"
    key_path = tmp / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return str(cert_path), str(key_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone S2 CEM engine")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443,
                        help="HTTPS port (S2 Connect requires TLS)")
    parser.add_argument("--tls-cert", help="TLS certificate PEM file")
    parser.add_argument("--tls-key", help="TLS private key PEM file")
    parser.add_argument("--pairing-token", default=None,
                        help="Fixed pairing token for development/testing")
    args = parser.parse_args()

    if args.tls_cert and args.tls_key:
        cert_path, key_path = args.tls_cert, args.tls_key
    elif _HAS_CRYPTO:
        cert_path, key_path = _generate_self_signed_cert()
        _LOGGER.warning("Self-signed cert generated — use --tls-cert/--tls-key in production")
    else:
        _LOGGER.error(
            "S2 Connect requires TLS.  Provide --tls-cert/--tls-key "
            "or install cryptography: pip install cryptography"
        )
        return

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_ctx.load_cert_chain(cert_path, key_path)

    cert_pem = Path(cert_path).read_bytes()
    cert_fingerprint = hashlib.sha256(
        x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
    ).digest() if _HAS_CRYPTO else (
        hashlib.sha256(cert_pem).digest()
    )

    node = S2NodeDescription(
        id=uuid.uuid4(),
        brand="S2 CEM Engine",
        type="CEM",
        modelName="standalone",
        role=Role.CEM,
    )
    endpoint = EndpointDescription(deployment=Deployment.LAN)

    # DNS-SD advertiser so RMs can discover this CEM on the LAN
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    # advertise the reachable address: the host name can resolve to another adapter the RM cannot route to
    pairing_url = f"https://{local_ip}:{args.port}/api/s2_pebc/pairing/"
    dns_sd = S2DnsSdAdvertiser(
        hostname=hostname,
        port=args.port,
        pairing_url=pairing_url,
        longpolling_url=pairing_url,
        ip_address=local_ip,
    )

    app_engine = S2CemApp(
        node,
        persistence=_FilePersistence(_STATE_FILE),
        event_bus=_PrintEventBus(),
        host_env=_ConsoleHostEnv(),
        scheduler=_AsyncScheduler(),
        endpoint_description=endpoint,
        supported_communication_protocols=[CommunicationProtocol.WEBSOCKET],
        supported_s2_message_versions=["0.0.2-beta"],
        certificate_fingerprint=cert_fingerprint,
        dns_sd_advertiser=dns_sd,
        pairing_token=args.pairing_token,
    )

    async def on_startup(web_app: web.Application) -> None:
        await app_engine.start()
        app_engine.register_routes(web_app)
        _LOGGER.info("S2 CEM running on https://%s:%d", args.host, args.port)

    async def on_shutdown(web_app: web.Application) -> None:
        await app_engine.stop()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    _register_control_api(app, app_engine)

    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx)


# ---------------------------------------------------------------------------
# Debug route helpers
# ---------------------------------------------------------------------------

def _get_rm_name(state, paired):
    if state.rm_details:
        return state.rm_details.name
    if paired:
        return paired.node_description.userDefinedName
    return None


def _get_rm_brand(state, paired):
    if state.rm_details:
        return state.rm_details.manufacturer
    if paired:
        return paired.node_description.brand
    return None


def _build_node_summary(nid, ws, state, paired):
    return {
        "node_id": str(nid),
        "connected": not ws.closed,
        "name": _get_rm_name(state, paired),
        "brand": _get_rm_brand(state, paired),
        "control_types": [ct.value for ct in state.rm_details.available_control_types] if state.rm_details else [],
        "power_constraints": {
            str(cid): {"consequence": pc.consequence_type.value, "ranges": len(pc.allowed_limit_ranges)}
            for cid, pc in (state.power_constraints or {}).items()
        },
        "energy_constraints": len(state.energy_constraints),
        "last_measurement": state.power_measurements,
    }


def _build_node_detail(nid, ws, state):
    return {
        "node_id": str(nid),
        "connected": ws is not None and not ws.closed,
        "rm_details": state.rm_details.to_json_dict(),
        "power_constraints": {str(cid): json.loads(pc.to_json()) for cid, pc in state.power_constraints.items()},
        "energy_constraints": [json.loads(ec.to_json()) for ec in state.energy_constraints.values()],
        "last_measurement": state.power_measurements,
    }


def _build_instruction_envelopes(data):
    envelopes = []
    for env in data.get("power_envelopes", []):
        envelopes.append({
            "id": str(uuid.uuid4()),
            "commodity_quantity": env["commodity_quantity"],
            "power_envelope_elements": [{
                "upper_limit": env["upper_limit"],
                "lower_limit": env["lower_limit"],
                "duration": data.get("duration", 3_600_000),
            }],
        })
    return envelopes


# ---------------------------------------------------------------------------
# Control API handlers
# ---------------------------------------------------------------------------

def _get_active_ws(engine: S2CemApp, node_id: uuid.UUID):
    ps = engine._pairing_server
    if ps is None:
        return None
    return ps.active_connections.get(node_id)


async def _json_body(req: web.Request) -> dict | None:
    """Parse the JSON request body, None if missing or invalid."""
    try:
        data = json.loads(await req.text())
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _list_nodes(engine: S2CemApp, _req: web.Request) -> web.Response:
    ps = engine._pairing_server
    nodes = []
    for nid, ws in (ps.active_connections if ps else {}).items():
        state = get_state(nid)
        paired = ps.paired_nodes.get(nid) if ps else None
        nodes.append(_build_node_summary(nid, ws, state, paired))
    return web.json_response(nodes)


def _node_detail(engine: S2CemApp, req: web.Request) -> web.Response:
    nid = uuid.UUID(req.match_info["node_id"])
    ps = engine._pairing_server
    ws = (ps.active_connections.get(nid) if ps else None)
    state = get_state(nid)
    if state.rm_details is None:
        return web.json_response({"error": "node not found"}, status=404)
    return web.json_response(_build_node_detail(nid, ws, state))


async def _send_instruction(engine: S2CemApp, req: web.Request) -> web.Response:
    data = await _json_body(req)
    if data is None:
        return web.json_response({"error": _INVALID_JSON_BODY}, status=400)
    ws = _get_active_ws(engine, uuid.UUID(data["node_id"]))
    if ws is None or ws.closed:
        return web.json_response({"error": "node not connected"}, status=404)

    envelopes = _build_instruction_envelopes(data)
    instr_id = str(uuid.uuid4())
    msg = {
        "message_type": "PEBC.Instruction",
        "message_id": str(uuid.uuid4()),
        "id": instr_id,
        "execution_time": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "abnormal_condition": data.get("abnormal_condition", False),
        "power_envelopes": envelopes,
        "power_constraints_id": data.get("power_constraints_id", str(uuid.uuid4())),
    }
    await ws.send_str(json.dumps(msg))
    return web.json_response({"instruction_id": instr_id})


async def _revoke_instruction(engine: S2CemApp, req: web.Request) -> web.Response:
    data = await _json_body(req)
    if data is None:
        return web.json_response({"error": _INVALID_JSON_BODY}, status=400)
    ws = _get_active_ws(engine, uuid.UUID(data["node_id"]))
    if ws is None or ws.closed:
        return web.json_response({"error": "node not connected"}, status=404)

    msg = {
        "message_type": "RevokeObject",
        "message_id": str(uuid.uuid4()),
        "object_id": data["instruction_id"],
        "object_type": "PEBC.Instruction",
    }
    await ws.send_str(json.dumps(msg))
    return web.json_response({"revoked": data["instruction_id"]})


def _generate_token(engine: S2CemApp, _req: web.Request) -> web.Response:
    ps = engine._pairing_server
    if ps is None:
        return web.json_response({"error": _PAIRING_SERVER_NOT_INIT}, status=503)
    info = ps.generate_pairing_info(f"https://{_req.host}/pairing/")
    _LOGGER.info("new pairing token: %s", info.token)
    return web.json_response({
        "token": info.token,
        "valid_until": info.validUntil.isoformat(),
    })


async def _initiate_pairing(engine: S2CemApp, req: web.Request) -> web.Response:
    ps = engine._pairing_server
    if ps is None:
        return web.json_response({"error": _PAIRING_SERVER_NOT_INIT}, status=503)
    data = await _json_body(req)
    if data is None:
        return web.json_response({"error": _INVALID_JSON_BODY}, status=400)
    try:
        node_id = uuid.UUID(data["clientNodeId"])
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": "clientNodeId required"}, status=400)
    triggered = ps.initiate_pairing(node_id, pairing_token=data.get("pairingToken"))
    return web.json_response({"triggered": triggered})


async def _longpoll_action(engine: S2CemApp, req: web.Request) -> web.Response:
    ps = engine._pairing_server
    if ps is None:
        return web.json_response({"error": _PAIRING_SERVER_NOT_INIT}, status=503)
    data = await _json_body(req)
    if data is None:
        return web.json_response({"error": _INVALID_JSON_BODY}, status=400)
    try:
        node_id = uuid.UUID(data["clientNodeId"])
        action = data["action"]
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": "clientNodeId and action required"}, status=400)
    if action not in ("sendNodeDescription", "preparePairing", "cancelPreparePairing", "requestPairing"):
        return web.json_response({"error": "invalid action"}, status=400)
    triggered = ps.longpoll_trigger(node_id, action)
    return web.json_response({"triggered": triggered})


def _register_control_api(app: web.Application, engine: S2CemApp) -> None:
    app.router.add_get("/api/nodes", lambda r: _list_nodes(engine, r))
    app.router.add_get("/api/nodes/{node_id}", lambda r: _node_detail(engine, r))
    app.router.add_post("/api/send-instruction", lambda r: _send_instruction(engine, r))
    app.router.add_post("/api/revoke-instruction", lambda r: _revoke_instruction(engine, r))
    app.router.add_get("/api/pairing-token", lambda r: _generate_token(engine, r))
    app.router.add_post("/api/initiate-pairing", lambda r: _initiate_pairing(engine, r))
    app.router.add_post("/api/longpoll-action", lambda r: _longpoll_action(engine, r))


if __name__ == "__main__":
    main()
