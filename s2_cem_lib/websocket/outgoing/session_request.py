# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""SessionRequest sender (CEM -> RM)."""
from __future__ import annotations

import logging
import uuid

from aiohttp import web
from s2python.common import SessionRequest, SessionRequestType

from . import tracked_send

_LOGGER = logging.getLogger(__name__)


async def send_session_request(
    ws: web.WebSocketResponse,
    request_type: SessionRequestType,
    node_id: str | None = None,
    diagnostic_label: str | None = None,
) -> None:
    """Send a SessionRequest to the RM.
    TERMINATE: CEM is shutting down. RM should acknowledge with ReceptionStatus OK.
    RECONNECT: CEM wants to restart the session from scratch. RM should acknowledge and expect a fresh reconnect soon.

    Connection closes regardless of request type.
    """
    msg = SessionRequest(
        message_id=uuid.uuid4(),
        request=request_type,
        diagnostic_label=diagnostic_label,
    )
    if node_id is not None:
        await tracked_send(ws, node_id, msg.message_id, msg.to_json())
    else:
        await ws.send_str(msg.to_json())
    _LOGGER.debug("Sent SessionRequest(%s) to node %s", request_type, node_id)
