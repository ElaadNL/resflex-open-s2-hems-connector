# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the UnpairButton HA entity."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from s2_cem_lib.const import DOMAIN
from s2python.common import Commodity, ResourceManagerDetails, Role
from src.button import UnpairButton


def _make_rm_details(name="My Inverter"):
    return ResourceManagerDetails(
        message_id=uuid.uuid4(),
        resource_id=uuid.uuid4(),
        name=name,
        roles=[Role(role="ENERGY_CONSUMER", commodity=Commodity.ELECTRICITY)],
        instruction_processing_delay=100,
        available_control_types=["POWER_ENVELOPE_BASED_CONTROL"],
        provides_forecast=False,
        provides_power_measurement_types=["ELECTRIC.POWER.L1"],
    )


def test_unpair_button_name_and_unique_id():
    node_id = uuid.uuid4()
    rm = _make_rm_details("Solar Panel")
    btn = UnpairButton(node_id, rm)
    assert btn.name == "Unpair Solar Panel"
    assert btn.unique_id == f"{DOMAIN}_{node_id}_unpair"


@pytest.mark.asyncio
async def test_async_press_calls_unpair_node_service():
    node_id = uuid.uuid4()
    btn = UnpairButton(node_id, _make_rm_details())
    btn.hass = MagicMock()
    btn.hass.services.async_call = AsyncMock()

    await btn.async_press()

    btn.hass.services.async_call.assert_awaited_once_with(
        DOMAIN, "unpair_node", {"node_id": str(node_id)}
    )
