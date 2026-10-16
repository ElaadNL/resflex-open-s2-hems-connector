# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the S2 WebSocket view (connection guards and message dispatch)."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from s2python.common import ReceptionStatusValues
from s2python.s2_validation_error import S2ValidationError

from s2_cem_lib.pairing.pairing_server import PairingServer
from s2_cem_lib.s2_cem.context import S2Context
from s2_cem_lib.tests.pairing.conftest import rejected_status
from s2_cem_lib.websocket.s2_errors import InvalidContentError, TemporaryError
from s2_cem_lib.websocket.ws_view import (
    S2WebSocketView,
    _extract_bearer_token,
)


def _make_mock_ctx(server=None):
    ctx = MagicMock(spec=S2Context)
    ctx.pairing_server = server
    return ctx


def make_request(server=None, bearer=None, remote="1.2.3.4"):
    """Build a minimal mock HTTP request."""
    request = MagicMock()
    request.app = {"hass": MagicMock(), "s2_ctx": _make_mock_ctx(server)}
    request.remote = remote
    request.headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return request


def _view(request=None):
    return S2WebSocketView(request or make_request())


# bearer token extraction (module-level function)

def test_extract_bearer_token_valid():
    request = MagicMock()
    request.headers = {"Authorization": "Bearer mytoken123"}
    assert _extract_bearer_token(request) == "mytoken123"


def test_extract_bearer_token_with_extra_spaces():
    request = MagicMock()
    request.headers = {"Authorization": "     Bearer       mytoken123     "}
    assert _extract_bearer_token(request) == "mytoken123"


def test_extract_bearer_token_with_lowercase():
    request = MagicMock()
    request.headers = {"Authorization": "bearer mytoken123"}
    assert _extract_bearer_token(request) == "mytoken123"


def test_extract_bearer_token_with_mixed_case_and_spaces():
    request = MagicMock()
    request.headers = {"Authorization": "   bEarER     mytoken123     "}
    assert _extract_bearer_token(request) == "mytoken123"


def test_extract_bearer_token_missing():
    request = MagicMock()
    request.headers = {}
    assert _extract_bearer_token(request) is None


# --- connection guard tests ---

@pytest.mark.asyncio
async def test_get_no_server_returns_503():
    """Integration not loaded returns 503."""
    request = make_request(server=None)
    request.app.pop("s2_ctx", None)
    response = await _view(request).get()
    assert response.status == 503


@pytest.mark.asyncio
async def test_get_missing_bearer_returns_401():
    """No Authorization header returns 401."""
    assert await rejected_status(_view(make_request(server=MagicMock(spec=PairingServer))).get()) == 401


@pytest.mark.asyncio
async def test_get_invalid_comm_token_returns_401():
    """Bearer token doesn't match any pending token returns 401."""
    server = MagicMock(spec=PairingServer)
    server.pending_tokens = []
    assert await rejected_status(_view(make_request(server=server, bearer="badtoken")).get()) == 401


# --- dispatch tests ---

@pytest.mark.asyncio
async def test_dispatch_invalid_json_sends_invalid_data():
    """Bad JSON returns ReceptionStatus INVALID_DATA (subject_message_id empty)."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    with patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), "not valid json{{{")
    mock_ack.assert_called_once_with(ws, uuid.UUID(int=0), ReceptionStatusValues.INVALID_DATA, "Not valid JSON")


@pytest.mark.asyncio
async def test_dispatch_schema_error_sends_invalid_message():
    """Message failing schema validation (missing required fields) returns ReceptionStatus INVALID_MESSAGE"""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    raw = json.dumps({"message_type": "ResourceManagerDetails"}) 
    with patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), raw)
    mock_ack.assert_called_once()
    args, _kwargs = mock_ack.call_args
    _ws, fallback_id, status = args
    assert status == ReceptionStatusValues.INVALID_MESSAGE
    assert fallback_id == uuid.UUID(int=0) # no message_id was included so fallback should be used


@pytest.mark.asyncio
async def test_dispatch_sends_reception_status_when_handler_returns_false():
    """If the handler doesn't send its own response, ReceptionStatus OK is sent."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    node_id = uuid.uuid4()
    valid_mid = "12345678-1234-5678-1234-567812345678"
    raw = json.dumps({
        "message_type": "PowerMeasurement",
        "message_id": valid_mid,
        "measurement_timestamp": "2024-01-01T00:00:00+00:00",
        "values": [{"commodity_quantity": "ELECTRIC.POWER.3_PHASE_SYMMETRIC", "value": 100.0}],
    })

    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(return_value=False)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", node_id, raw)

    mock_ack.assert_called_once_with(ws, uuid.UUID(valid_mid), ReceptionStatusValues.OK)


@pytest.mark.asyncio
async def test_dispatch_no_reception_status_when_handler_returns_true():
    """If the handler already responded (returns True), no extra ReceptionStatus is sent."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    node_id = uuid.uuid4()

    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(return_value=True)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", node_id, _VALID_RAW)

    mock_ack.assert_not_called()


_VALID_MID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_VALID_RAW = json.dumps({
    "message_type": "PowerMeasurement",
    "message_id": _VALID_MID,
    "measurement_timestamp": "2024-01-01T00:00:00+00:00",
    "values": [{"commodity_quantity": "ELECTRIC.POWER.3_PHASE_SYMMETRIC", "value": 100.0}],
})


@pytest.mark.asyncio
async def test_dispatch_schema_mismatch_sends_invalid_message():
    """S2ValidationError from a handler returns ReceptionStatus INVALID_MESSAGE."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    exc = S2ValidationError(None, (), "incorrect schema details format")
    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(side_effect=exc)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), _VALID_RAW)
    mock_ack.assert_called_once_with(ws, uuid.UUID(_VALID_MID), ReceptionStatusValues.INVALID_MESSAGE, str(exc))


@pytest.mark.asyncio
async def test_dispatch_invalid_content_sends_invalid_content():
    """InvalidContentError from a handler returns ReceptionStatus INVALID_CONTENT."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    exc = InvalidContentError("references unknown element")
    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(side_effect=exc)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), _VALID_RAW)
    mock_ack.assert_called_once_with(ws, uuid.UUID(_VALID_MID), ReceptionStatusValues.INVALID_CONTENT, str(exc))


@pytest.mark.asyncio
async def test_dispatch_temporary_error_sends_temporary_error():
    """TemporaryError from a handler returns ReceptionStatus TEMPORARY_ERROR."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    exc = TemporaryError("resource temporarily unavailable")
    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(side_effect=exc)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), _VALID_RAW)
    mock_ack.assert_called_once_with(ws, uuid.UUID(_VALID_MID), ReceptionStatusValues.TEMPORARY_ERROR, str(exc))


@pytest.mark.asyncio
async def test_dispatch_unexpected_exception_sends_permanent_error():
    """An unhandled exception from a handler returns ReceptionStatus PERMANENT_ERROR."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    exc = RuntimeError("unexpected errorr")
    with patch("s2_cem_lib.websocket.ws_view.dispatch", new=AsyncMock(side_effect=exc)), \
         patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", uuid.uuid4(), _VALID_RAW)
    mock_ack.assert_called_once_with(ws, uuid.UUID(_VALID_MID), ReceptionStatusValues.PERMANENT_ERROR, str(exc))


@pytest.mark.asyncio
async def test_dispatch_log_single_sends_ok():
    """A single log frame should be acknowledged with OK using its id."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    node_id = uuid.uuid4()
    bid = "11111111-1111-1111-1111-111111111111"
    raw = json.dumps({"type": "log", "id": bid, "level": "INFO", "message": "test log"})

    with patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", node_id, raw)

    mock_ack.assert_called_once()
    args, _kwargs = mock_ack.call_args
    _ws, subject_id, status = args
    assert status == ReceptionStatusValues.OK
    assert subject_id == uuid.UUID(bid)


@pytest.mark.asyncio
async def test_dispatch_log_batch_too_large_sends_temporary_error():
    """A log_batch with >128 messages should be rejected with TEMPORARY_ERROR."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    node_id = uuid.uuid4()
    msgs = [{"level": "INFO", "message": f"m{i}"} for i in range(129)]
    bid = "22222222-2222-2222-2222-222222222222"
    raw = json.dumps({"type": "log_batch", "batch_id": bid, "messages": msgs})

    with patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", node_id, raw)

    mock_ack.assert_called_once()
    args, _kwargs = mock_ack.call_args
    _ws, subject_id, status = args
    assert status == ReceptionStatusValues.TEMPORARY_ERROR
    assert subject_id == uuid.UUID(bid)

@pytest.mark.asyncio
async def test_dispatch_log_warn_mirrors_and_fires_event(caplog):
    """WARN-level RM logs should be mirrored and fired through the event bus."""
    ws = MagicMock()
    ws.send_str = AsyncMock()
    node_id = uuid.uuid4()
    bid = "55555555-5555-5555-5555-555555555555"
    raw = json.dumps({"type": "log", "id": bid, "level": "WARN", "message": "something concerning"})

    from s2_cem_lib.websocket.node_state import get_state
    event_bus = MagicMock()
    get_state(node_id).event_bus = event_bus

    caplog.set_level("WARNING")
    with patch("s2_cem_lib.websocket.ws_view.send_reception_status", new=AsyncMock()) as mock_ack:
        await _view()._dispatch(ws, "remote", node_id, raw)

    mock_ack.assert_called_once()
    event_bus.send.assert_called_once()
    assert any("RM log (node" in rec.getMessage() for rec in caplog.records)
