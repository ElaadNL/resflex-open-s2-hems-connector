# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for sending SessionRequest messages over the websocket."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from s2python.common import SessionRequestType

from s2_cem_lib.websocket.node_state import clear_state
from s2_cem_lib.websocket.outgoing.session_request import send_session_request


@pytest.mark.asyncio
async def test_send_session_request_terminate_sends_correct_message():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()

    await send_session_request(ws, SessionRequestType.TERMINATE, node_id=node_id)

    ws.send_str.assert_awaited_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "SessionRequest"
    assert payload["request"] == "TERMINATE"
    clear_state(node_id)

@pytest.mark.asyncio
async def test_send_session_request_reconnect_sends_correct_message():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()

    await send_session_request(ws, SessionRequestType.RECONNECT, node_id=node_id)

    ws.send_str.assert_awaited_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "SessionRequest"
    assert payload["request"] == "RECONNECT"
    clear_state(node_id)
