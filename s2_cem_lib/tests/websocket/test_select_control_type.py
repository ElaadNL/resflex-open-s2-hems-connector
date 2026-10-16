# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for sending SelectControlType messages over the websocket."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from s2_cem_lib.websocket.node_state import clear_state
from s2_cem_lib.websocket.outgoing.select_control_type import send_select_control_type


@pytest.mark.asyncio
async def test_send_select_control_type_sends_correct_message():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()

    await send_select_control_type(ws, node_id)

    ws.send_str.assert_awaited_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "SelectControlType"
    assert payload["control_type"] == "POWER_ENVELOPE_BASED_CONTROL"
    clear_state(node_id)