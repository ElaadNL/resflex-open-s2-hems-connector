# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles RevokeObject (RM -> CEM). Removes previously stored S2 objects from node state."""
from __future__ import annotations

import logging

from aiohttp import web
from s2python.common import RevokableObjects, RevokeObject

from ...const import SIGNAL_ENERGY_CONSTRAINTS, SIGNAL_POWER_CONSTRAINTS
from ..node_state import get_state

_LOGGER = logging.getLogger(__name__)


def _revoke_constraint(state, obj_id: str, constraints: dict, signal: str, node_id: str, remote: str, label: str) -> None:
    removed = constraints.pop(obj_id, None)
    if removed is not None:
        _LOGGER.debug("RevokeObject: removed %s %s from node %s (reported by %s)", label, obj_id, node_id, remote)
        if state.event_bus is not None:
            state.event_bus.send(signal, node_id, state.rm_details, dict(constraints))
    else:
        _LOGGER.warning("RevokeObject: %s %s not found on node %s (already gone?) (reported by %s)", label, obj_id, node_id, remote)


def handle_revoke_object(
    _ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    revoke: RevokeObject,
) -> None:
    """Handles RevokeObject messages from RM.
    This removes a previously sent object (such as PEBC.PowerConstraints) from local state.
    Removing an instruction such as PEBC.PowerConstraint means that the CEM must wait until
    new constraints are received before it can send further instructions to that RM."""
    state = get_state(node_id)
    obj_id = revoke.object_id
    obj_type = revoke.object_type

    if obj_type == RevokableObjects.PEBC_PowerConstraints:
        _revoke_constraint(state, obj_id, state.power_constraints, SIGNAL_POWER_CONSTRAINTS, node_id, remote, "PEBC.PowerConstraints")
    elif obj_type == RevokableObjects.PEBC_EnergyConstraint:
        _revoke_constraint(state, obj_id, state.energy_constraints, SIGNAL_ENERGY_CONSTRAINTS, node_id, remote, "PEBC.EnergyConstraint")
    elif obj_type == RevokableObjects.PEBC_Instruction:
        _LOGGER.warning("RevokeObject: PEBC.Instruction revoke from node %s not yet tracked (id=%s) (reported by %s)", node_id, obj_id, remote)
    elif obj_type.value == "PowerForecast":
        count = len(state.forecast_elements)
        state.forecast_elements.clear()
        _LOGGER.debug("RevokeObject: cleared %d forecast element(s) for node %s (reported by %s)", count, node_id, remote)
    else:
        _LOGGER.warning("RevokeObject: unsupported type %s from node %s (id=%s) (reported by %s)", obj_type, node_id, obj_id, remote)
