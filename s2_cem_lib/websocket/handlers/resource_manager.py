# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles ResourceManagerDetails (RM -> CEM) and triggers SelectControlType (CEM -> RM)."""
from __future__ import annotations

import logging

from aiohttp import web
from s2python.common import ControlType, ReceptionStatusValues, ResourceManagerDetails

from ...const import SIGNAL_NODE_CONNECTED
from ..node_state import get_state
from ..outgoing.reception_status import send_reception_status
from ..outgoing.select_control_type import send_select_control_type

_LOGGER = logging.getLogger(__name__)


async def handle_resource_manager_details(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    rm_details: ResourceManagerDetails,
) -> bool:
    """
    RM sends its capabilities. CEM acknowledges then responds with SelectControlType.

    Flow:
      RM -> CEM: ResourceManagerDetails
      CEM -> RM: ReceptionStatus OK
      CEM -> RM: SelectControlType (POWER_ENVELOPE_BASED_CONTROL)
    """
    _LOGGER.info(
        "ResourceManagerDetails from %s (node %s): name=%s, control_types=%s, measurement_types=%s",
        remote, node_id, rm_details.name, rm_details.available_control_types,
        rm_details.provides_power_measurement_types,
    )

    if ControlType.POWER_ENVELOPE_BASED_CONTROL not in rm_details.available_control_types:
        _LOGGER.error(
            "RM %s (node %s) does not support PEBC. Available: %s",
            remote, node_id, rm_details.available_control_types,
        )
        # Cannot control RM so we send permanent error ReceptionStatus and close the connection.
        await send_reception_status(ws, rm_details.message_id, ReceptionStatusValues.PERMANENT_ERROR)
        await ws.close()
    else:
        # Store RM details so external consumers and other handlers can reference them.
        state = get_state(node_id)
        state.rm_details = rm_details
        # ResourceManagerDetails signals the start of a new S2 session.
        # Clear any constraints left over from a previous session so stale entries
        # cannot suppress new constraint values in the sensors.
        state.power_constraints.clear()
        state.energy_constraints.clear()

        # Acknowledge receipt before sending SelectControlType by sending a ReceptionStatus with status OK.
        await send_reception_status(ws, rm_details.message_id, ReceptionStatusValues.OK)
        await send_select_control_type(ws, node_id)

        # Notify listeners so they can act on the new RM connection.
        if state.event_bus is not None:
            state.event_bus.send(SIGNAL_NODE_CONNECTED, node_id, rm_details)
        else:
            _LOGGER.warning("event_bus not set on node state for %s — telemetry will not be published", node_id)

    return True  # we sent ReceptionStatus manually for both possible cases so no need for caller to send it again.
