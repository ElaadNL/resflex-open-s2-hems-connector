# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the S2 Handshake handling (return HandshakeResponse, select protocol version)."""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from s2python.common import Handshake

from s2_cem_lib.websocket.handlers import _handle_handshake

_CEM_SUPPORTED_VERSIONS = ["0.0.2-beta", "0.0.1-beta"]


def _make_ws():
    ws = MagicMock()
    ws.send_str = AsyncMock()
    return ws


def _handshake_msg(role="RM", versions=None):
    return Handshake(
        message_id=uuid.uuid4(),
        role=role,
        supported_protocol_versions=versions or _CEM_SUPPORTED_VERSIONS,
    )


@pytest.mark.asyncio
async def test_handshake_non_handshake_returns_false():
    """Non-Handshake message is not handled."""
    ws = _make_ws()
    result = await _handle_handshake(ws, "remote", uuid.uuid4(), MagicMock())
    assert result is False


@pytest.mark.asyncio
async def test_handshake_sends_response_and_reception_status():
    """Valid RM handshake sends HandshakeResponse + ReceptionStatus, returns True."""
    ws = _make_ws()
    msg = _handshake_msg("RM", ["0.03", "0.02", "0.01"])
    result = await _handle_handshake(ws, "remote", uuid.uuid4(), msg)
    assert result is True
    assert ws.send_str.call_count == 2
    # First: HandshakeResponse
    sent1 = json.loads(ws.send_str.call_args_list[0][0][0])
    assert sent1["message_type"] == "HandshakeResponse"
    assert sent1["selected_protocol_version"] in _CEM_SUPPORTED_VERSIONS
    # Second: ReceptionStatus
    sent2 = json.loads(ws.send_str.call_args_list[1][0][0])
    assert sent2["message_type"] == "ReceptionStatus"
    assert sent2["status"] == "OK"