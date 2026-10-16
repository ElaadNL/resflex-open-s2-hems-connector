# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles ReceptionStatus messages (RM -> CEM) with retry/backoff logic."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from s2python.common import ReceptionStatus, ReceptionStatusValues

from ...const import MAX_BACKOFF_RETRY_DELAY_SECONDS
from ..node_state import get_state

_LOGGER = logging.getLogger(__name__)


def _handle_temporary_error(
    ws: web.WebSocketResponse,
    node_id: str,
    status: ReceptionStatus,
    state,
) -> None:
    entry = state.sent_messages.get(status.subject_message_id)
    if entry is None or state.scheduler is None:
        _LOGGER.warning(
            "TEMPORARY_ERROR for message %s from node %s — not tracked, cannot retry",
            status.subject_message_id, node_id,
        )
        return
    raw_json, retry_count, old_task = entry
    # Cancel old task so that only one retry is ever in flight at a time for this message.
    # Without this, repeated TEMPORARY_ERROR responses before a retry fires would accumulate
    # tasks that all send the same stale message, potentially overwriting a newer instruction.
    if old_task is not None and not old_task.done():
        old_task.cancel()
    retry_count += 1
    delay = min(2 ** retry_count, MAX_BACKOFF_RETRY_DELAY_SECONDS)
    _LOGGER.warning(
        "TEMPORARY_ERROR for message %s from node %s. Scheduling retry #%d in %.0fs",
        status.subject_message_id, node_id, retry_count, delay,
    )
    captured_ws = ws
    captured_json = raw_json
    captured_msg_id = status.subject_message_id

    async def _retry() -> None:
        await asyncio.sleep(delay)
        # If the entry was cleared (PERMANENT_ERROR, disconnect, or superseded by a
        # newer instruction) the retry is no longer valid.
        if captured_msg_id not in get_state(node_id).sent_messages:
            _LOGGER.debug(
                "Retry for message %s on node %s cancelled: entry was cleared",
                captured_msg_id, node_id,
            )
            return
        await captured_ws.send_str(captured_json)
        _LOGGER.debug("Retried message %s to node %s", captured_msg_id, node_id)

    task = state.scheduler.create_task(_retry())
    state.sent_messages[status.subject_message_id] = (raw_json, retry_count, task)


def _handle_permanent_error(
    node_id: str,
    status: ReceptionStatus,
    state,
) -> None:
    state.sent_messages.pop(status.subject_message_id, None)
    matched = [p for p in state.pending_instructions if p.message_id == status.subject_message_id]
    if matched:
        state.pending_instructions = [p for p in state.pending_instructions if p.message_id != status.subject_message_id]
        _LOGGER.error(
            "Instruction message %s permanently rejected by node %s — clearing pending instruction",
            status.subject_message_id, node_id,
        )
    else:
        _LOGGER.error(
            "Message %s permanently rejected by node %s: %s",
            status.subject_message_id, node_id, status.diagnostic_label,
        )


def handle_reception_status(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    status: ReceptionStatus,
) -> None:
    """
    RM acknowledges a message the CEM sent.

    Flow:
      CEM -> RM: <any message>
      RM -> CEM: ReceptionStatus

    Status values:
        OK              = accepted by RM. Do nothing.
        TEMPORARY_ERROR = RM could not process it right now so we retry with exponential backoff.
        PERMANENT_ERROR = RM will never accept this message. Log and clear any pending instructions that match.

        INVALID_DATA /
        INVALID_MESSAGE /
        INVALID_CONTENT = Don't retry, must be a CEM bug. Log error.

    We must never send a ReceptionStatus in response to a ReceptionStatus.
    """
    state = get_state(node_id)

    if status.status == ReceptionStatusValues.OK:
        _LOGGER.debug(
            "ReceptionStatus OK from %s (node %s) for message %s",
            remote, node_id, status.subject_message_id,
        )
        state.sent_messages.pop(status.subject_message_id, None)
    elif status.status == ReceptionStatusValues.TEMPORARY_ERROR:
        _LOGGER.warning(
            "ReceptionStatus %s from %s (node %s) for message %s: %s",
            status.status, remote, node_id, status.subject_message_id, status.diagnostic_label,
        )
        _handle_temporary_error(ws, node_id, status, state)
    elif status.status == ReceptionStatusValues.PERMANENT_ERROR:
        _LOGGER.warning(
            "ReceptionStatus %s from %s (node %s) for message %s: %s",
            status.status, remote, node_id, status.subject_message_id, status.diagnostic_label,
        )
        _handle_permanent_error(node_id, status, state)
    else:
        # INVALID_DATA, INVALID_MESSAGE, INVALID_CONTENT — do not retry, must be a CEM bug.
        _LOGGER.error(
            "ReceptionStatus %s for message %s from node %s indicates a CEM bug: %s",
            status.status, status.subject_message_id, node_id, status.diagnostic_label,
        )
        state.sent_messages.pop(status.subject_message_id, None)
