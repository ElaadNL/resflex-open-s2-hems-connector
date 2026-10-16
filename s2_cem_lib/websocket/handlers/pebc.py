# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles PEBC-specific messages (RM -> CEM)."""
from __future__ import annotations

import logging

from aiohttp import web
from s2python.pebc import PEBCEnergyConstraint, PEBCPowerConstraints

from ...const import MAX_ENERGY_CONSTRAINTS_PER_NODE, MAX_POWER_CONSTRAINTS_PER_NODE, SIGNAL_ENERGY_CONSTRAINTS, SIGNAL_POWER_CONSTRAINTS
from ..node_state import get_state, prune_expired_constraints
from ..s2_errors import InvalidContentError

_LOGGER = logging.getLogger(__name__)


def handle_power_constraints(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    constraints: PEBCPowerConstraints,
) -> None:
    """
    RM sends its power envelope constraints.
    CEM stores these per node so they can be used when issuing PEBC.Instruction.

    A node may have multiple active PowerConstraints with different ids.
    Each commodity quantity may have separate UPPER_LIMIT and LOWER_LIMIT entries.
    The CEM MUST NOT send instructions if no PowerConstraints are currently stored for the node.

    Expired constraints are dropped on receipt. The store is capped at MAX_POWER_CONSTRAINTS_PER_NODE, and a new constraint sent while it is full is rejected.

    Flow:
      RM -> CEM: PEBC.PowerConstraints
      CEM may later send: PEBC.Instruction (within the allowed limit ranges)
    """
    state = get_state(node_id)

    pruned = prune_expired_constraints(state.power_constraints)
    if pruned:
        _LOGGER.debug("Dropped %d expired PEBC.PowerConstraints for node %s", pruned, node_id)

    if constraints.id not in state.power_constraints and len(state.power_constraints) >= MAX_POWER_CONSTRAINTS_PER_NODE:
        raise InvalidContentError(f"PEBC.PowerConstraints store full for node {node_id}, max {MAX_POWER_CONSTRAINTS_PER_NODE}")

    state.power_constraints[constraints.id] = constraints
    _LOGGER.info(
        "PEBC.PowerConstraints from %s (node %s): id=%s, consequence=%s, %d limit range(s) across %d commodity quantity(ies)",
        remote, node_id, constraints.id, constraints.consequence_type,
        len(constraints.allowed_limit_ranges),
        len({r.commodity_quantity for r in constraints.allowed_limit_ranges}),
    )
    if state.event_bus is not None:
        state.event_bus.send(SIGNAL_POWER_CONSTRAINTS, node_id, state.rm_details, dict(state.power_constraints))


def handle_energy_constraint(
    ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    constraint: PEBCEnergyConstraint,
) -> None:
    """
    RM optionally sends an energy budget constraint (average power over a window).
    CEM should respect this when issuing instructions.

    Expired constraints are dropped on receipt. The store is capped at MAX_ENERGY_CONSTRAINTS_PER_NODE, and a new constraint sent while it is full is rejected.

    Flow:
      RM -> CEM: PEBC.EnergyConstraint  (optional, may arrive any time)
    """
    state = get_state(node_id)

    pruned = prune_expired_constraints(state.energy_constraints)
    if pruned:
        _LOGGER.debug("Dropped %d expired PEBC.EnergyConstraint for node %s", pruned, node_id)

    if constraint.id not in state.energy_constraints and len(state.energy_constraints) >= MAX_ENERGY_CONSTRAINTS_PER_NODE:
        raise InvalidContentError(f"PEBC.EnergyConstraint store full for node {node_id}, max {MAX_ENERGY_CONSTRAINTS_PER_NODE}")

    state.energy_constraints[constraint.id] = constraint
    _LOGGER.info(
        "PEBC.EnergyConstraint from %s (node %s): id=%s, upper=%.1fW, lower=%.1fW",
        remote, node_id, constraint.id, constraint.upper_average_power,
        constraint.lower_average_power,
    )
    if state.event_bus is not None:
        state.event_bus.send(SIGNAL_ENERGY_CONSTRAINTS, node_id, state.rm_details, dict(state.energy_constraints))


