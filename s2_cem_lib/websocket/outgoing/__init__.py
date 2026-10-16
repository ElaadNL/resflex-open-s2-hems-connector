# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Outgoing S2 message helpers.

All CEM to RM senders should use tracked_send instead of ws.send_str directly
so that TEMPORARY_ERROR retries always resend the exact same JSON payload.
"""
from __future__ import annotations

from uuid import UUID

from aiohttp import web

from ..node_state import get_state


async def tracked_send(ws: web.WebSocketResponse, node_id: UUID, msg_id: UUID, json_str: str) -> None:
    """Send json over web socket and register it for potential TEMPORARY_ERROR retry if necessary.

    The message is stored in the node's sent_messages dict under msg_id with
    retry_count=0. Incremented on each retry to increase backoff exponentially.
    If handle_reception_status receives a TEMPORARY_ERROR for this message_id, 
    it will resend the same exact message with exponential back-off if TEMPORARY_ERROR is received again.
    On OK, PERMANENT_ERROR or INVALID ReceptionStatus the entry is removed by the handler. Only TEMPORARY_ERROR gets a retry.
    """
    get_state(node_id).sent_messages[msg_id] = (json_str, 0, None)
    await ws.send_str(json_str)
