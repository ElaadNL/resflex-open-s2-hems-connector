# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for reception status updates over websocket"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from s2python.common import ReceptionStatusValues

from s2_cem_lib.websocket.outgoing.reception_status import send_reception_status


@pytest.mark.asyncio
async def test_send_reception_status_sends_correct_json():
    ws = AsyncMock()
    subject_id = uuid.uuid4()

    await send_reception_status(ws, subject_id, ReceptionStatusValues.OK, diagnostic_label="all good")

    ws.send_str.assert_called_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "ReceptionStatus"
    assert payload["subject_message_id"] == str(subject_id)
    assert payload["status"] == "OK"
    assert payload["diagnostic_label"] == "all good"
