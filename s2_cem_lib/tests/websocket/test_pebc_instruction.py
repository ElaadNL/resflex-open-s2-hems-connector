# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for sending PEBC.Instruction messages over the websocket."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from s2python.common import CommodityQuantity

from s2_cem_lib.websocket.node_state import PowerEnvelopeSpec, clear_state
from s2_cem_lib.websocket.outgoing.pebc_instruction import send_pebc_instruction


def _make_constraint(quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC):
    limit_range = MagicMock()
    limit_range.commodity_quantity = quantity
    constraint = MagicMock()
    constraint.id = uuid.uuid4()
    constraint.allowed_limit_ranges = [limit_range]
    return constraint


@pytest.mark.asyncio
async def test_send_pebc_instruction_calls_send_str_with_correct_limits():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()
    constraint = _make_constraint()
    envelopes = [PowerEnvelopeSpec(
        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
        upper_limit=3000.0,
        lower_limit=500.0,
    )]

    await send_pebc_instruction(ws, node_id, envelopes=envelopes, constraint=constraint)

    ws.send_str.assert_awaited_once()
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["message_type"] == "PEBC.Instruction"
    envelope = payload["power_envelopes"][0]
    element = envelope["power_envelope_elements"][0]
    assert element["upper_limit"] == 3000.0
    assert element["lower_limit"] == 500.0
    clear_state(node_id)


@pytest.mark.asyncio
async def test_send_pebc_instruction_abnormal_condition_false_by_default():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()
    constraint = _make_constraint()
    envelopes = [PowerEnvelopeSpec(
        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
        upper_limit=1000.0,
        lower_limit=0.0,
    )]
    await send_pebc_instruction(ws, node_id, envelopes=envelopes, constraint=constraint)
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["abnormal_condition"] is False
    clear_state(node_id)


@pytest.mark.asyncio
async def test_send_pebc_instruction_abnormal_condition_true():
    node_id = uuid.uuid4()
    ws = MagicMock()
    ws.send_str = AsyncMock()
    constraint = _make_constraint()
    envelopes = [PowerEnvelopeSpec(
        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
        upper_limit=4000.0,
        lower_limit=0.0,
    )]
    await send_pebc_instruction(ws, node_id, envelopes=envelopes, constraint=constraint, abnormal_condition=True)
    payload = json.loads(ws.send_str.call_args[0][0])
    assert payload["abnormal_condition"] is True
    clear_state(node_id)