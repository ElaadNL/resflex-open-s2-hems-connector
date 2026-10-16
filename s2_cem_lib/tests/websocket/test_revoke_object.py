# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for sending RevokeObject messages over the websocket."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from s2_cem_lib.websocket.node_state import clear_state
from s2_cem_lib.websocket.outgoing.revoke_object import send_revoke_instruction


@pytest.mark.asyncio
async def test_send_revoke_instruction_sends_correct_message():
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()

    await send_revoke_instruction(ws, node_id, instruction_id)

    ws.send_str.assert_awaited_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "RevokeObject"
    assert payload["object_type"] == "PEBC.Instruction"
    assert payload["object_id"] == str(instruction_id)
    clear_state(node_id)
