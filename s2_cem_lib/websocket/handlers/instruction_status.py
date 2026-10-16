# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles InstructionStatusUpdate (RM -> CEM) with retry/backoff logic."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from s2python.common import InstructionStatus, InstructionStatusUpdate

from ...const import MAX_BACKOFF_RETRY_DELAY_SECONDS
from ..node_state import get_state
from ..outgoing.pebc_instruction import send_pebc_instruction

_LOGGER = logging.getLogger(__name__)


def handle_instruction_status_update(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    update: InstructionStatusUpdate,
) -> None:
    """
    RM reports the status of an instruction the CEM previously sent.
    CEM should track lifecycle: NEW -> ACCEPTED/REJECTED -> STARTED -> SUCCEEDED/ABORTED.

    Flow:
      CEM -> RM: PEBC.Instruction
      RM -> CEM: InstructionStatusUpdate  (one or more, per transition)

    Possible status values:
        NEW:        we log and do nothing else. RM will later send updates.
        ABORTED:    we should recheck power + energy constraints and try again with exponential backoff.
                    Aborted means the RM had a problem executing the instruction but the instruction was valid (within RM specified limits)
        REJECTED:   we should recheck power + energy constraints and try again. Also with exponential backoff in case RM is bugged.
                    If our instructions are within the limits then it should never be rejected, so a rejection means something went wrong on the RM side
        STARTED:    we can just log. No further action.
        REVOKED:    we can just log since the revocation came from the CEM. This is a confirmation message from the RM the instruciton was successfully revoked.
        SUCCEEDED:  we can just log. No further action is needed.
    """
    _LOGGER.debug(
        "InstructionStatusUpdate from %s (node %s): instruction=%s, status=%s",
        remote, node_id, update.instruction_id, update.status_type,
    )

    # NEW, STARTED, REVOKED, SUCCEEDED are only logged. No action is needed.
    if update.status_type in {
        InstructionStatus.NEW,
        InstructionStatus.STARTED,
        InstructionStatus.REVOKED,
        InstructionStatus.SUCCEEDED,
    }:
        return

    if update.status_type == InstructionStatus.ABORTED or update.status_type == InstructionStatus.REJECTED:
        state = get_state(node_id)
        pending = next((p for p in state.pending_instructions if p.instruction_id == update.instruction_id), None)
        if pending is None or state.scheduler is None:
            _LOGGER.warning(
                "Instruction %s %s on node %s but no pending instruction to retry",
                update.instruction_id, update.status_type, node_id,
            )
            return
        pending.retry_count += 1
        delay = min(2 ** pending.retry_count, MAX_BACKOFF_RETRY_DELAY_SECONDS)
        _LOGGER.warning(
            "Instruction %s %s by node %s. Scheduling retry #%d in %.0fs",
            update.instruction_id, update.status_type, node_id, pending.retry_count, delay,
        )
        captured_ws = ws
        captured_pending = pending
        captured_instruction_id = pending.instruction_id

        async def _retry() -> None:
            await asyncio.sleep(delay)
            current_state = get_state(node_id)
            # If this instruction was removed from pending_instructions
            # (for example: superseded by a newer instruction that also overlaps in time) 
            # cancel the retry.
            if not any(p.instruction_id == captured_instruction_id for p in current_state.pending_instructions):
                _LOGGER.debug(
                    "Retry for instruction %s on node %s cancelled: instruction no longer pending",
                    captured_instruction_id, node_id,
                )
                return
            if not current_state.power_constraints:
                _LOGGER.warning("Retry #%d for node %s cancelled: no power constraints available", captured_pending.retry_count, node_id)
                return
            # Use the most recently received constraint
            constraint = next(iter(current_state.power_constraints.values()))
            await send_pebc_instruction(
                captured_ws,
                node_id,
                captured_pending.envelopes,
                constraint,
                captured_pending.duration_ms,
                retry_count=captured_pending.retry_count,
                abnormal_condition=captured_pending.abnormal_condition,
            )

        state.scheduler.create_task(_retry())
