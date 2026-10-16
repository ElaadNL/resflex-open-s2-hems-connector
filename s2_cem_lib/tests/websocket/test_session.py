# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for handle_session_request (RM -> CEM)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from s2python.common import ReceptionStatusValues, SessionRequest, SessionRequestType

from s2_cem_lib.websocket.handlers.session import handle_session_request


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
@patch("s2_cem_lib.websocket.handlers.session.send_reception_status", new_callable=AsyncMock)
async def test_session_request_sends_reception_status_and_closes_terminate(mock_send):
    """Session request should send ReceptionStatus OK and close the connection for TERMINATE."""
    ws = _make_ws()
    req = SessionRequest(message_id=uuid.uuid4(), request=SessionRequestType.TERMINATE)
    result = await handle_session_request(ws, "remote", "node-1", req)
    mock_send.assert_awaited_once_with(ws, req.message_id, ReceptionStatusValues.OK)
    ws.close.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
@patch("s2_cem_lib.websocket.handlers.session.send_reception_status", new_callable=AsyncMock)
async def test_session_request_sends_reception_status_and_closes_reconnect(mock_send):
    """Session request should send ReceptionStatus OK and close the connection for RECONNECT."""
    ws = _make_ws()
    req = SessionRequest(message_id=uuid.uuid4(), request=SessionRequestType.RECONNECT)
    result = await handle_session_request(ws, "remote", "node-1", req)
    mock_send.assert_awaited_once_with(ws, req.message_id, ReceptionStatusValues.OK)
    ws.close.assert_awaited_once()
    assert result is True
