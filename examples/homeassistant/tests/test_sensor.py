# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the S2 PEBC sensor platform."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from s2_cem_lib.const import DOMAIN, SIGNAL_NODE_CONNECTED
from s2python.common import Commodity, ResourceManagerDetails, Role
from src.sensor import (
    NodeIDSensor,
    S2EnergyConstraintSensor,
    S2PowerConstraintSensor,
    S2PowerSensor,
    _format_commodity_quantity,
    _restore_known_sensors,
    async_setup_entry,
)


def _make_rm_details(quantities=None):
    return ResourceManagerDetails(
        message_id=uuid.uuid4(),
        resource_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        name="Mock RM",
        manufacturer="MockCo",
        model="MockRM-1000",
        firmware_version="1.0.0",
        instruction_processing_delay=100,
        available_control_types=["POWER_ENVELOPE_BASED_CONTROL"],
        provides_forecast=False,
        provides_power_measurement_types=quantities or ["ELECTRIC.POWER.L1"],
        roles=[Role(role="ENERGY_CONSUMER", commodity=Commodity.ELECTRICITY)],
    )


def _make_sensor(commodity="ELECTRIC.POWER.L1"):
    return S2PowerSensor("node-1", _make_rm_details([commodity]), commodity)


# S2PowerSensor initial state must be unavailable without a value
def test_sensor_starts_unavailable():
    sensor = _make_sensor()
    assert sensor._attr_available is False
    assert sensor._attr_native_value is None


def test_sensor_unique_id_and_name():
    sensor = _make_sensor("ELECTRIC.POWER.L2")
    assert sensor._attr_unique_id == f"{DOMAIN}_node-1_ELECTRIC.POWER.L2"
    assert sensor._attr_name == "Mock RM Electric Power L2"


# ensure measurement is set and sensor is immediately marked as available when first measurement is received
def test_handle_measurement_updates_value():
    sensor = _make_sensor()
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_measurement("ELECTRIC.POWER.L1", 1500.0)

    assert sensor._attr_native_value == 1500.0
    assert sensor._attr_available is True
    sensor.async_write_ha_state.assert_called_once()

# ensure wrong commodity is ignored
def test_handle_measurement_wrong_commodity_ignored():
    sensor = _make_sensor("ELECTRIC.POWER.L1")
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_measurement("ELECTRIC.POWER.L2", 999.0)

    assert sensor._attr_native_value is None
    sensor.async_write_ha_state.assert_not_called()

# WS disconnect marks sensor as unavailable; native_value kept for RestoreSensor cross-restart restore
def test_handle_disconnected_marks_unavailable():
    sensor = _make_sensor()
    sensor.async_write_ha_state = MagicMock()
    sensor._attr_native_value = 1000.0
    sensor._attr_available = True

    sensor._handle_disconnected()

    assert sensor._attr_available is False
    assert sensor._attr_native_value == 1000.0  # preserved for RestoreSensor
    sensor.async_write_ha_state.assert_called_once()


# Ensure entity entry system works
@pytest.mark.asyncio
async def test_setup_entry_creates_sensors_on_node_connected():
    """async_setup_entry subscribes to SIGNAL_NODE_CONNECTED and creates one sensor per quantity given."""
    hass = MagicMock()
    entry = MagicMock()
    async_add_entities = MagicMock()

    created_callback = None

    def fake_dispatcher_connect(h, signal, cb):
        nonlocal created_callback
        if signal == SIGNAL_NODE_CONNECTED:
            created_callback = cb
        return MagicMock()  

    hass.loop.call_soon_threadsafe.side_effect = lambda fn: fn()

    with patch("src.sensor.Store") as mock_store_cls, \
         patch("src.sensor.async_dispatcher_connect", side_effect=fake_dispatcher_connect):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        await async_setup_entry(hass, entry, async_add_entities)

    rm_details = _make_rm_details(["ELECTRIC.POWER.L1", "ELECTRIC.POWER.L2"])
    created_callback("node-1", rm_details)

    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 3  # 2 power sensors + 1 NodeIDSensor
    power_sensors = [e for e in entities if hasattr(e, "_commodity_quantity")]
    assert {e._commodity_quantity for e in power_sensors} == {"ELECTRIC.POWER.L1", "ELECTRIC.POWER.L2"}
    node_id_sensors = [e for e in entities if hasattr(e, "_node_id") and not hasattr(e, "_commodity_quantity")]
    assert len(node_id_sensors) == 1
    assert node_id_sensors[0]._attr_native_value == "node-1"


# S2PowerConstraintSensor._handle_update used to compare UUID directly against a str node_id,
# causing updates to be silently dropped. Test that UUID node_id is accepted.
def test_power_constraint_sensor_handle_update_accepts_uuid_node_id():
    node_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    sensor = S2PowerConstraintSensor(str(node_id), _make_rm_details(), "ELECTRIC.POWER.L1", "upper", {})
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_update(node_id, None, {})  # UUID, not str

    sensor.async_write_ha_state.assert_called_once()


def test_power_constraint_sensor_handle_update_wrong_node_ignored():
    sensor = S2PowerConstraintSensor("node-1", _make_rm_details(), "ELECTRIC.POWER.L1", "upper", {})
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_update(uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"), None, {})

    sensor.async_write_ha_state.assert_not_called()


# _format_commodity_quantity: "CommodityQuantity.ELECTRIC_POWER_L1" prefix form (from s2python enum repr)
def test_format_commodity_quantity_strips_enum_prefix():
    assert _format_commodity_quantity("CommodityQuantity.ELECTRIC_POWER_L1") == "Electric Power L1"


# _restore_known_sensors: creates NodeIDSensor + S2PowerSensor per stored commodity quantity and marks node registered
def test_restore_known_sensors_creates_entities():
    stored = {
        "node-1": {
            "name": "Mock RM",
            "resource_id": "00000000-0000-0000-0000-000000000001",
            "manufacturer": "MockCo",
            "model": "MockRM",
            "firmware_version": "1.0.0",
            "serial_number": None,
            "provides_power_measurement_types": ["ELECTRIC.POWER.L1"],
        }
    }
    registered: set = set()
    add_entities = MagicMock()

    _restore_known_sensors(stored, registered, add_entities)

    assert "node-1" in registered
    add_entities.assert_called_once()
    entities = add_entities.call_args[0][0]
    assert any(isinstance(e, NodeIDSensor) for e in entities)
    assert any(isinstance(e, S2PowerSensor) for e in entities)


def test_energy_constraint_sensor_handle_update_sets_value():
    sensor = S2EnergyConstraintSensor("node-1", _make_rm_details(), "ELECTRIC.POWER.L1", "upper", {})
    sensor.async_write_ha_state = MagicMock()

    constraint = MagicMock()
    constraint.commodity_quantity = "ELECTRIC.POWER.L1"
    constraint.upper_average_power = 3000.0

    sensor._handle_update("node-1", None, {"c1": constraint})

    assert sensor._attr_native_value == 3000.0
    assert sensor._attr_available is True
    sensor.async_write_ha_state.assert_called_once()
