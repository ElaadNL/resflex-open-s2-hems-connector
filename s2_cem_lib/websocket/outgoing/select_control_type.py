# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""SelectControlType sender. Send the mutual control type to the RM (takes place after handshake)"""
from __future__ import annotations

import logging
import uuid

from aiohttp import web
from s2python.common import ControlType, SelectControlType

from . import tracked_send

_LOGGER = logging.getLogger(__name__)


async def send_select_control_type(
    ws: web.WebSocketResponse,
    node_id: str,
) -> None:
    """Send SelectControlType to the RM. Agreeing on which control mode to use for this session (PEBC)."""
    msg = SelectControlType(message_id=uuid.uuid4(), control_type=ControlType.POWER_ENVELOPE_BASED_CONTROL)
    await tracked_send(ws, node_id, msg.message_id, msg.to_json())
    _LOGGER.debug("Sent SelectControlType (%s) to node %s", ControlType.POWER_ENVELOPE_BASED_CONTROL, node_id)
