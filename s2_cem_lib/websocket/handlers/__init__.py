# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2 message handler registry and dispatcher."""
from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from uuid import UUID

from aiohttp import web
from s2python.common import (
    Handshake,
    HandshakeResponse,
    InstructionStatusUpdate,
    PowerForecast,
    PowerMeasurement,
    ReceptionStatus,
    ReceptionStatusValues,
    ResourceManagerDetails,
    RevokeObject,
    SessionRequest,
)
from s2python.message import S2Message
from s2python.pebc import PEBCEnergyConstraint, PEBCPowerConstraints

from ...const import S2_SUPPORTED_VERSIONS
from ..outgoing.reception_status import send_reception_status
from .instruction_status import handle_instruction_status_update
from .measurements import handle_power_forecast, handle_power_measurement
from .pebc import handle_energy_constraint, handle_power_constraints
from .reception_status import handle_reception_status
from .resource_manager import handle_resource_manager_details
from .revoke_object import handle_revoke_object
from .session import handle_session_request

_LOGGER = logging.getLogger(__name__)

# Maps S2 message class to handlers for incoming messages from RM
Handler = Callable[[web.WebSocketResponse, str, UUID, S2Message], Awaitable[bool | None] | (bool | None)]

async def _handle_handshake(ws: web.WebSocketResponse, remote: str, node_id: UUID, msg: S2Message) -> bool:
    """Respond to Handshake with HandshakeResponse. Backwards-compatible with RMs that still send it."""
    if not isinstance(msg, Handshake):
        return False
    rm_versions = msg.supported_protocol_versions or []
    common = [v for v in S2_SUPPORTED_VERSIONS if v in rm_versions]
    selected = common[0] if common else S2_SUPPORTED_VERSIONS[0]
    await ws.send_str(HandshakeResponse(message_id=uuid.uuid4(), selected_protocol_version=selected).to_json())
    await send_reception_status(ws, msg.message_id, ReceptionStatusValues.OK)
    _LOGGER.info("Handshake from %s (node %s): selected version %s", remote, node_id, selected)
    return True


_HANDLERS: dict[type, Handler] = {
    Handshake: _handle_handshake,
    ResourceManagerDetails: handle_resource_manager_details,
    PEBCPowerConstraints: handle_power_constraints,
    PEBCEnergyConstraint: handle_energy_constraint,
    PowerMeasurement: handle_power_measurement,
    PowerForecast: handle_power_forecast,
    InstructionStatusUpdate: handle_instruction_status_update,
    SessionRequest: handle_session_request,
    ReceptionStatus: handle_reception_status,
    RevokeObject: handle_revoke_object,
}


async def dispatch(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: UUID,
    msg: S2Message,
) -> bool | None:
    """Route a parsed S2 message to its handler.
    Returns True if the handler already sent a response (caller skips ReceptionStatus).
    """
    handler = _HANDLERS.get(type(msg))
    if handler is None:
        _LOGGER.warning("Unhandled message type '%s' from %s (node %s)", type(msg).__name__, remote, node_id)
        return None
    result = handler(ws, remote, node_id, msg)
    if inspect.isawaitable(result): # handle async and sync handlers
        return await result
    return result
