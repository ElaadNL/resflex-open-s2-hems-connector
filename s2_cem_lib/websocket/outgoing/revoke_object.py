# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Sends a RevokeObject for a PEBC.Instruction to an RM."""
from __future__ import annotations

import logging
import uuid
from uuid import UUID

from aiohttp import web
from s2python.common import RevokableObjects, RevokeObject

from . import tracked_send

_LOGGER = logging.getLogger(__name__)


async def send_revoke_instruction(ws: web.WebSocketResponse, node_id: str, instruction_id: UUID) -> None:
    """Send a RevokeObject(PEBC.Instruction) to the RM."""
    msg = RevokeObject(
        message_id=uuid.uuid4(),
        object_type=RevokableObjects.PEBC_Instruction,
        object_id=instruction_id,
    )
    await tracked_send(ws, node_id, msg.message_id, msg.to_json())
    _LOGGER.debug("Sent RevokeObject PEBC.Instruction %s to node %s", instruction_id, node_id)
