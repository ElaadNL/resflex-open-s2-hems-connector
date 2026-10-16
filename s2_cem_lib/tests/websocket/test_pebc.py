# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for PEBC handler functions (power and energy contraints)"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from s2_cem_lib.const import MAX_ENERGY_CONSTRAINTS_PER_NODE, MAX_POWER_CONSTRAINTS_PER_NODE
from s2_cem_lib.websocket.handlers.pebc import handle_energy_constraint, handle_power_constraints
from s2_cem_lib.websocket.node_state import clear_state, get_state
from s2_cem_lib.websocket.s2_errors import InvalidContentError


def test_handle_power_constraints_stores_in_state():
    """check if power constraint is stored in HA state"""
    node_id = uuid.uuid4()
    constraint_id = uuid.uuid4()
    constraints = MagicMock(id=constraint_id, valid_until=None, allowed_limit_ranges=[], consequence_type="VANISH")
    handle_power_constraints(MagicMock(), "remote", node_id, constraints)
    assert get_state(node_id).power_constraints[constraint_id] is constraints
    clear_state(node_id)


def test_handle_energy_constraint_stores_in_state():
    """check if energy constraint is stored in HA state"""
    node_id = uuid.uuid4()
    constraint_id = uuid.uuid4()
    constraint = MagicMock(id=constraint_id, valid_until=None, upper_average_power=5000.0, lower_average_power=0.0)
    handle_energy_constraint(MagicMock(), "remote", node_id, constraint)
    assert get_state(node_id).energy_constraints[constraint_id] is constraint
    clear_state(node_id)


def test_power_constraints_store_is_capped():
    """A node cannot accumulate power constraints beyond the cap."""
    node_id = uuid.uuid4()
    clear_state(node_id)

    for _ in range(MAX_POWER_CONSTRAINTS_PER_NODE):
        handle_power_constraints(MagicMock(), "remote", node_id, MagicMock(id=uuid.uuid4(), valid_until=None))

    with pytest.raises(InvalidContentError):
        handle_power_constraints(MagicMock(), "remote", node_id, MagicMock(id=uuid.uuid4(), valid_until=None))

    assert len(get_state(node_id).power_constraints) == MAX_POWER_CONSTRAINTS_PER_NODE
    clear_state(node_id)


def test_energy_constraints_store_is_capped():
    """A node cannot accumulate energy constraints beyond the cap."""
    node_id = uuid.uuid4()
    clear_state(node_id)

    for _ in range(MAX_ENERGY_CONSTRAINTS_PER_NODE):
        handle_energy_constraint(MagicMock(), "remote", node_id, MagicMock(id=uuid.uuid4(), valid_until=None))

    with pytest.raises(InvalidContentError):
        handle_energy_constraint(MagicMock(), "remote", node_id, MagicMock(id=uuid.uuid4(), valid_until=None))

    assert len(get_state(node_id).energy_constraints) == MAX_ENERGY_CONSTRAINTS_PER_NODE
    clear_state(node_id)