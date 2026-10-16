# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles SessionRequest messages (RM -> CEM)."""
from __future__ import annotations

import logging

from aiohttp import web
from s2python.common import ReceptionStatusValues, SessionRequest, SessionRequestType

from ..outgoing.reception_status import send_reception_status

_LOGGER = logging.getLogger(__name__)


async def handle_session_request(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    req: SessionRequest,
) -> bool:
    """
    RM requests a session action: RECONNECT or TERMINATE.

      TERMINATE: RM is shutting down. CEM acknowledges with ReceptionStatus OK and closes the connection.
      RECONNECT: RM wants to restart the session from scratch. CEM acknowledges with ReceptionStatus OK
                  and closes the connection. the RM will then reconnect and a new handshake + session setup will begin.

    In both cases the CEM must send ReceptionStatus OK  before it closes the WS connection.

    Flow:
      RM -> CEM: SessionRequest
      CEM -> RM: ReceptionStatus OK
      CEM closes the WebSocket
    """
    _LOGGER.debug(
        "SessionRequest from %s (node %s): request=%s, label=%s",
        remote, node_id, req.request, req.diagnostic_label,
    )
    await send_reception_status(ws, req.message_id, ReceptionStatusValues.OK)
    if req.request == SessionRequestType.TERMINATE:
        _LOGGER.info("Closing connection to %s (node %s): RM requested TERMINATE", remote, node_id)
        await ws.close()
    elif req.request == SessionRequestType.RECONNECT:
        _LOGGER.info("Closing connection to %s (node %s): RM requested RECONNECT. Expecting fresh reconnect", remote, node_id)
        await ws.close()
    return True
