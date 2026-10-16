# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""PEBC instruction sender. Sends PEBC.Instruction to an RM to set upper/lower power limits for a specified duration."""
from __future__ import annotations

import datetime
import logging
import uuid

from aiohttp import web
from s2python.pebc import PEBCInstruction, PEBCPowerConstraints, PEBCPowerEnvelope, PEBCPowerEnvelopeElement

from ...const import DEFAULT_INSTRUCTION_DURATION_MS
from ..node_state import PendingInstruction, PowerEnvelopeSpec, get_state
from . import tracked_send

_LOGGER = logging.getLogger(__name__)


async def send_pebc_instruction(
    ws: web.WebSocketResponse,
    node_id: str,
    envelopes: list[PowerEnvelopeSpec],
    constraint: PEBCPowerConstraints,
    duration_ms: int = DEFAULT_INSTRUCTION_DURATION_MS,
    retry_count: int = 0,
    abnormal_condition: bool = False,
    execution_time: str | datetime.datetime | None = None,
) -> None:
    """Send a PEBC.Instruction to the RM.

    Each PowerEnvelopeSpec in ``envelopes`` maps to one power envelope in the message,
    allowing different upper/lower limits per commodity quantity (phase).
    The RM will respond with InstructionStatusUpdate messages which are handled accordingly in the session handler.
    """
    built_envelopes = [
        PEBCPowerEnvelope(
            id=str(uuid.uuid4()),
            commodity_quantity=spec.commodity_quantity,
            power_envelope_elements=[
                PEBCPowerEnvelopeElement(
                    duration=duration_ms,
                    upper_limit=spec.upper_limit,
                    lower_limit=spec.lower_limit,
                )
            ],
        )
        for spec in envelopes
    ]
    # Determine execution_time: accept an ISO-8601 string or a datetime, default to now (UTC)
    if isinstance(execution_time, str):
        # Accept trailing Z (UTC) by converting to +00:00 for fromisoformat
        et_str = execution_time
        if et_str.endswith("Z"):
            et_str = et_str[:-1] + "+00:00"
        execution_time = datetime.datetime.fromisoformat(et_str)
    if execution_time is None:
        execution_time = datetime.datetime.now(datetime.UTC)
    # Ensure timezone-aware UTC
    if execution_time.tzinfo is None:
        execution_time = execution_time.replace(tzinfo=datetime.UTC)

    msg = PEBCInstruction(
        message_id=uuid.uuid4(),
        id=uuid.uuid4(),
        execution_time=execution_time,
        abnormal_condition=abnormal_condition,
        power_constraints_id=constraint.id,
        power_envelopes=built_envelopes,
    )
    new_end = execution_time + datetime.timedelta(milliseconds=duration_ms)

    state = get_state(node_id)
    # Remove any existing pending instructions whose time window overlaps with the new one.
    # Non-overlapping (future) instructions can continue retrying as normal
    overlapping = [
        p for p in state.pending_instructions
        if p.execution_time < new_end and execution_time < p.execution_time + datetime.timedelta(milliseconds=p.duration_ms)
    ]
    for p in overlapping:
        state.sent_messages.pop(p.message_id, None)
    state.pending_instructions = [p for p in state.pending_instructions if p not in overlapping]

    await tracked_send(ws, node_id, msg.message_id, msg.to_json())
    state.pending_instructions.append(PendingInstruction(
        envelopes=envelopes,
        duration_ms=duration_ms,
        constraint=constraint,
        instruction_id=msg.id,
        message_id=msg.message_id,
        execution_time=execution_time,
        retry_count=retry_count,
        abnormal_condition=abnormal_condition,
    ))
    _LOGGER.debug(
        "Sent PEBC.Instruction to node %s: %d envelope(s) duration=%dms constraints_id=%s instruction_id=%s",
        node_id, len(envelopes), duration_ms, constraint.id, msg.id,
    )
