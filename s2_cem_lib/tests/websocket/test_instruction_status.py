# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for instruction status update handling (ABORTED/REJECTED retry logic)."""
from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from s2python.common import CommodityQuantity, InstructionStatus, InstructionStatusUpdate

from s2_cem_lib.websocket.handlers.instruction_status import handle_instruction_status_update
from s2_cem_lib.websocket.node_state import PendingInstruction, PowerEnvelopeSpec, clear_state, get_state
from s2_cem_lib.websocket.outgoing.pebc_instruction import send_pebc_instruction


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


def test_instruction_status_update_aborted_schedules_retry():
    """ABORTED status should schedule a retry via hass.async_create_task."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.ABORTED,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_called_once()
    state.scheduler.create_task.call_args[0][0].close()
    clear_state(node_id)

def test_instruction_status_update_rejected_schedules_retry():
    """REJECTED status should schedule a retry via hass.async_create_task."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.REJECTED,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_called_once()
    state.scheduler.create_task.call_args[0][0].close()
    clear_state(node_id)


def test_instruction_status_update_accepted_logs_and_does_nothing():
    """ACCEPTED status should just log and not schedule a retry."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.ACCEPTED,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_not_called()
    clear_state(node_id)

def test_instruction_status_update_succeeded_logs_and_does_nothing():
    """SUCCEEDED status should just log and not schedule a retry."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.SUCCEEDED,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_not_called()
    clear_state(node_id)


def test_instruction_status_update_revoked_logs_and_does_nothing():
    """REVOKED status should just log and not schedule a retry."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.REVOKED,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_not_called()
    clear_state(node_id)

def test_instruction_status_update_new_logs_and_does_nothing():
    """NEW status should just log and not schedule a retry."""
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    state = get_state(node_id)
    state.scheduler = MagicMock()
    state.pending_instructions = [MagicMock(instruction_id=instruction_id, retry_count=0)]
    state.power_constraints = {uuid.uuid4(): MagicMock(allowed_limit_ranges=[])}

    update = InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id,
        status_type=InstructionStatus.NEW,
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    handle_instruction_status_update(_make_ws(), "remote", node_id, update)
    state.scheduler.create_task.assert_not_called()
    clear_state(node_id)


@pytest.mark.asyncio
async def test_overlapping_new_instruction_removes_old_and_retry_does_not_resend():
    """
    Scenario: instruction A fails -> retry scheduled -> new instruction B with an
    overlapping time window is sent -> A is removed from pending_instructions ->
    A's retry detects this and does NOT resend.
    """
    node_id = uuid.uuid4()
    instruction_id_a = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC)

    state = get_state(node_id)
    state.scheduler = MagicMock()
    constraint = MagicMock()
    constraint.id = uuid.uuid4()
    state.power_constraints = {constraint.id: constraint}

    # A: time window [now, now+2h]
    state.pending_instructions = [PendingInstruction(
        envelopes=[PowerEnvelopeSpec(commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1, upper_limit=1000.0, lower_limit=0.0)],
        duration_ms=7_200_000,
        constraint=constraint,
        instruction_id=instruction_id_a,
        message_id=uuid.uuid4(),
        execution_time=now,
    )]

    # A is ABORTED -> retry scheduled
    handle_instruction_status_update(_make_ws(), "remote", node_id, InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id_a,
        status_type=InstructionStatus.ABORTED,
        timestamp=now,
    ))
    retry_a = state.scheduler.create_task.call_args[0][0]

    # Send B with execution_time=now and duration=1h -> overlaps with A time window
    constraint_b = MagicMock()
    constraint_b.id = uuid.uuid4()
    await send_pebc_instruction(
        _make_ws(), node_id,
        [PowerEnvelopeSpec(commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1, upper_limit=500.0, lower_limit=0.0)],
        constraint_b,
        duration_ms=3_600_000,
    )

    # A must be gone; only B remains
    assert not any(p.instruction_id == instruction_id_a for p in state.pending_instructions)
    assert len(state.pending_instructions) == 1

    # A's retry fires but detects A is no longer pending -> DOES NOT resend
    with patch("s2_cem_lib.websocket.handlers.instruction_status.send_pebc_instruction", new=AsyncMock()) as mock_send, \
         patch("asyncio.sleep", new=AsyncMock()):
        await retry_a
    mock_send.assert_not_called()

    clear_state(node_id)


@pytest.mark.asyncio
async def test_non_overlapping_instructions_both_keep_retrying():
    """
    Scenario: instruction A fails -> retry scheduled -> new instruction B in a
    non-overlapping future time window is sent -> A stays in pending_instructions ->
    B also fails -> both A and B have active retries that resend independently.
    """
    node_id = uuid.uuid4()
    instruction_id_a = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC)

    state = get_state(node_id)
    state.scheduler = MagicMock()
    constraint = MagicMock()
    constraint.id = uuid.uuid4()
    state.power_constraints = {constraint.id: constraint}

    envelopes = [PowerEnvelopeSpec(commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1, upper_limit=1000.0, lower_limit=0.0)]

    # A: time window [3h ago, 2h ago]. in the past, will not overlap with anything sent now
    three_hours_ago = now - datetime.timedelta(hours=3)
    state.pending_instructions = [PendingInstruction(
        envelopes=envelopes,
        duration_ms=3_600_000,
        constraint=constraint,
        instruction_id=instruction_id_a,
        message_id=uuid.uuid4(),
        execution_time=three_hours_ago,
    )]

    # A is ABORTED -> retry scheduled
    handle_instruction_status_update(_make_ws(), "remote", node_id, InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id_a,
        status_type=InstructionStatus.ABORTED,
        timestamp=now,
    ))
    retry_a = state.scheduler.create_task.call_args[0][0]
    state.scheduler.reset_mock()

    # Send B (execution_time=now, 1h) -> [now, now+1h] does NOT overlap with A's [3h_ago, 2h_ago]
    await send_pebc_instruction(_make_ws(), node_id, envelopes, constraint, duration_ms=3_600_000)

    # A must still be present. Both A and B are pending
    assert any(p.instruction_id == instruction_id_a for p in state.pending_instructions)
    assert len(state.pending_instructions) == 2

    # B is also ABORTED -> retry scheduled for B
    instruction_id_b = next(p.instruction_id for p in state.pending_instructions if p.instruction_id != instruction_id_a)
    handle_instruction_status_update(_make_ws(), "remote", node_id, InstructionStatusUpdate(
        message_id=uuid.uuid4(),
        instruction_id=instruction_id_b,
        status_type=InstructionStatus.ABORTED,
        timestamp=now,
    ))
    retry_b = state.scheduler.create_task.call_args[0][0]

    # A's retry fires -> A is still pending -> resends
    with patch("s2_cem_lib.websocket.handlers.instruction_status.send_pebc_instruction", new=AsyncMock()) as mock_send, \
         patch("asyncio.sleep", new=AsyncMock()):
        await retry_a
    mock_send.assert_called_once()

    # B's retry fires -> B is still pending -> resends
    with patch("s2_cem_lib.websocket.handlers.instruction_status.send_pebc_instruction", new=AsyncMock()) as mock_send, \
         patch("asyncio.sleep", new=AsyncMock()):
        await retry_b
    mock_send.assert_called_once()

    clear_state(node_id)