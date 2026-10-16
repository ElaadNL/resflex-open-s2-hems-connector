# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""WebSocket view. The endpoint that S2 Resource Managers connect to.

Auth failures are raised so middleware sees the attempt.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import aiohttp
from aiohttp import web
from s2python.common import ReceptionStatus, ReceptionStatusValues
from s2python.s2_parser import S2Parser
from s2python.s2_validation_error import S2ValidationError

from ..const import (
    SIGNAL_NODE_DISCONNECTED_FMT,
    SIGNAL_RM_LOG,
)
from ..pairing.pairing_server import PairingServer
from .handlers import dispatch
from .node_state import get_state
from .outgoing.reception_status import send_reception_status
from .s2_errors import InvalidContentError, TemporaryError

_LOGGER = logging.getLogger(__name__)


def _parse_uuid_or_zero(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return uuid.UUID(int=0)


def _is_log_frame(parsed: object) -> bool:
    return isinstance(parsed, dict) and (
        parsed.get("type") in ("log", "log_batch") or parsed.get("message_type") == "Log.Batch"
    )


def _collect_msgs(parsed: dict) -> list:
    return [parsed] if parsed.get("type") == "log" else parsed.get("messages") or []


def _is_receptionstatus_message(m: dict) -> bool:
    text = m.get("message", "")
    return m.get("message_type") == "ReceptionStatus" or (isinstance(text, str) and "ReceptionStatus" in text)


def _mirror_rm_log(level: str, text: str, node_id) -> None:
    try:
        lvl = (level or "INFO").upper()
        if lvl in ("WARN", "WARNING"):
            _LOGGER.warning("RM log (node %s) %s: %s", node_id, level, text)
        elif lvl == "ERROR":
            _LOGGER.error("RM log (node %s) %s: %s", node_id, level, text)
    except (KeyError, ValueError, AttributeError):
        _LOGGER.debug("Failed to mirror RM log to logger")


async def _handle_log_frame_if_present(ws: web.WebSocketResponse, node_id, parsed) -> bool:
    """Handle a quick log or log_batch frame if present. Returns True when handled."""
    if not _is_log_frame(parsed):
        return False

    msgs = _collect_msgs(parsed)
    if not isinstance(msgs, list) or len(msgs) > 128:
        bid = parsed.get("batch_id") or str(uuid.uuid4())
        bid_uuid = _parse_uuid_or_zero(bid)
        await send_reception_status(ws, bid_uuid, ReceptionStatusValues.TEMPORARY_ERROR)
        return True

    ack_id = parsed.get("message_id") or parsed.get("batch_id") or parsed.get("id") or str(uuid.uuid4())

    state = get_state(node_id)
    for m in msgs:
        if _is_receptionstatus_message(m):
            continue
        if state.event_bus is not None:
            state.event_bus.send(
                SIGNAL_RM_LOG,
                node_id,
                m.get("level", "INFO"),
                m.get("message", ""),
                m.get("source"),
                m.get("timestamp"),
            )
        _mirror_rm_log(m.get("level", "INFO"), m.get("message", ""), node_id)

    ack_uuid = _parse_uuid_or_zero(ack_id)
    await send_reception_status(ws, ack_uuid, ReceptionStatusValues.OK)
    return True


async def _parse_s2_message_or_ack(parsed, raw: str, ws: web.WebSocketResponse, remote: str, node_id):
    """Parse raw S2 message, returning the message or sending an INVALID_MESSAGE ack and returning None."""
    try:
        return S2Parser.parse_as_any_message(raw)
    except S2ValidationError as exc:
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        _LOGGER.warning(
            "Schema validation failed for message from %s (node %s): %s%s",
            remote,
            node_id,
            exc,
            f" | cause: {cause}" if cause else "",
        )
        message_id_str = parsed.get("message_id", "") if isinstance(parsed, dict) else ""
        fallback_id = _parse_uuid_or_zero(message_id_str)
        await send_reception_status(ws, fallback_id, ReceptionStatusValues.INVALID_MESSAGE, diagnostic_label=str(exc))
        return None


def _extract_bearer_token(request: web.Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    match = re.search(r"(?i)bearer\s+(\S+)", auth_header)
    if match:
        return match.group(1).strip()
    return None


def _find_pending_token(pairing_server: PairingServer, comm_token_str: str):
    """Find a pending token matching the given commToken string."""
    return next(
        (p for p in pairing_server.pending_tokens if p.new_comm_token is not None and p.new_comm_token.check(comm_token_str)),
        None,
    )


class S2WebSocketView(web.View):
    """WebSocket endpoint that S2 Resource Managers connect to."""

    async def get(self) -> web.StreamResponse:
        """Handle an incoming WebSocket connection."""
        ctx = self.request.app.get("s2_ctx")
        if ctx is None:
            return web.Response(status=503, reason="Engine not initialized")
        pairing_server = ctx.pairing_server

        comm_token_str = _extract_bearer_token(self.request)
        if not comm_token_str:
            _LOGGER.warning("WebSocket connection rejected: missing Bearer token")
            raise web.HTTPUnauthorized()

        pending = _find_pending_token(pairing_server, comm_token_str)
        if pending is None:
            _LOGGER.warning("WebSocket connection rejected: invalid or expired commToken")
            raise web.HTTPUnauthorized()

        pending.node.access_token = pending.new_access_token
        pairing_server.pending_tokens.remove(pending)

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(self.request)

        remote = self.request.remote
        node_id = pending.node.node_id
        state = get_state(node_id)
        state.event_bus = ctx.event_bus
        state.scheduler = ctx.scheduler
        pairing_server.active_connections[node_id] = ws
        _LOGGER.info("S2 RM connected from %s (node %s)", remote, node_id)

        # trusting a self-signed root certificate: verify the
        # server certificate hasn't changed since pairing.
        if not pairing_server.verify_certificate(node_id):
            _LOGGER.warning(
                "S2 PEBC: TLS certificate changed since pairing for node %s — re-pairing required",
                node_id,
            )

        await self._run_message_loop(ws, pairing_server, node_id, remote)
        return ws

    async def _run_message_loop(
        self,
        ws: web.WebSocketResponse,
        pairing_server: PairingServer,
        node_id,
        remote: str,
    ) -> None:
        """Run the WebSocket message loop until the connection closes or the engine is stopped."""
        try:
            async for msg in ws:
                ctx = self.request.app.get("s2_ctx")
                if ctx is None or ctx.pairing_server is None:
                    await ws.close(message=b"Engine not initialized")
                    break
                if msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error from %s: %s", remote, ws.exception())
                    continue
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                await self._dispatch(ws, remote, node_id, msg.data)
        finally:
            self._teardown_connection(pairing_server, node_id, ws, remote)

    def _teardown_connection(self, pairing_server, node_id, ws, remote):
        """Clean up after the message loop exits."""
        if pairing_server.active_connections.get(node_id) is ws:
            pairing_server.active_connections.pop(node_id, None)
            state = get_state(node_id)
            if state.event_bus is not None:
                state.event_bus.send(SIGNAL_NODE_DISCONNECTED_FMT.format(node_id=node_id))
        else:
            _LOGGER.debug(
                "S2 RM stale connection cleanup skipped for node %s (newer connection already active)",
                node_id,
            )
        _LOGGER.info("S2 RM disconnected from %s (node %s)", remote, node_id)

    async def _dispatch(
        self,
        ws: web.WebSocketResponse,
        remote: str,
        node_id,
        raw: str,
    ) -> None:
        """Dispatch an S2 message received after handshake completion."""
        _LOGGER.debug("S2 message from %s (node %s): %s", remote, node_id, raw)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Invalid JSON from %s (node %s)", remote, node_id)
            await send_reception_status(ws, uuid.UUID(int=0), ReceptionStatusValues.INVALID_DATA, "Not valid JSON")
            return

        if await _handle_log_frame_if_present(ws, node_id, parsed):
            return

        # parse S2 message and handle early validation errors
        msg = await _parse_s2_message_or_ack(parsed, raw, ws, remote, node_id)
        if msg is None:
            return

        message_id: uuid.UUID = getattr(msg, "message_id", None) or uuid.UUID(int=0)
        message_type: str = getattr(msg, "message_type", type(msg).__name__)

        try:
            responded = await dispatch(ws, remote, node_id, msg)
            if not responded and not isinstance(msg, ReceptionStatus):
                await send_reception_status(ws, message_id, ReceptionStatusValues.OK)
        except S2ValidationError as exc:
            _LOGGER.warning("Schema validation failed for %s from %s: %s", message_type, remote, exc)
            await send_reception_status(ws, message_id, ReceptionStatusValues.INVALID_MESSAGE, str(exc))
        except InvalidContentError as exc:
            _LOGGER.warning("Invalid content in %s from %s: %s", message_type, remote, exc)
            await send_reception_status(ws, message_id, ReceptionStatusValues.INVALID_CONTENT, str(exc))
        except TemporaryError as exc:
            _LOGGER.warning("Temporary error handling %s from %s: %s", message_type, remote, exc)
            await send_reception_status(ws, message_id, ReceptionStatusValues.TEMPORARY_ERROR, str(exc))
        except Exception as exc:
            _LOGGER.exception("Unrecoverable error handling %s from %s", message_type, remote)
            await send_reception_status(ws, message_id, ReceptionStatusValues.PERMANENT_ERROR, str(exc))
