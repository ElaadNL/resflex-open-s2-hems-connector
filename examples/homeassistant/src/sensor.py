# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2 PEBC sensor platform.

This creates SensorEntity entities within Home Assistant for each RM and for each power type the RM declares in its ResourceManagerDetails message.
The sensor entities are created at that point with device info, but start as unavailable until the first PowerMeasurement arrives.

This HA sensor entry continuously listens for new RM nodes that are created (when a new RM connects and sends its ResourceManagerDetails) and creates entities accordingly.

If the RM disconnects from the websocket, the sensor entity is marked as unavailable until it reconnects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from s2_cem_lib.const import (
    DOMAIN,
    SIGNAL_ENERGY_CONSTRAINTS,
    SIGNAL_NODE_CONNECTED,
    SIGNAL_NODE_DISCONNECTED_FMT,
    SIGNAL_POWER_CONSTRAINTS,
    SIGNAL_POWER_MEASUREMENT_FMT,
)
from s2python.common import ResourceManagerDetails

_LOGGER = logging.getLogger(__name__)

_KNOWN_NODES_STORE_KEY = f"{DOMAIN}_known_nodes"
_KNOWN_NODES_STORE_VERSION = 1


def _format_commodity_quantity(cq: str) -> str:
    """Convert 'CommodityQuantity.ELECTRIC_POWER_L1' or 'ELECTRIC.POWER.L1' to 'Electric Power L1'."""
    cq = cq.removeprefix("CommodityQuantity.")
    return cq.replace(".", " ").replace("_", " ").title()


def _device_info(node_id: str, rm_details) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, node_id)},
        name=rm_details.name or str(rm_details.resource_id),
        manufacturer=rm_details.manufacturer,
        model=rm_details.model,
        sw_version=rm_details.firmware_version,
        serial_number=rm_details.serial_number,
    )


@dataclass
class _StoredRmDetails:
    name: str | None
    resource_id: str
    manufacturer: str | None
    model: str | None
    firmware_version: str | None
    serial_number: str | None
    provides_power_measurement_types: list[str]


def _restore_known_sensors(stored_nodes: dict, registered_nodes: set, async_add_entities: AddEntitiesCallback) -> None:
    for node_id, node_data in stored_nodes.items():
        registered_nodes.add(node_id)
        proxy = _StoredRmDetails(**node_data)
        sensors: list[SensorEntity] = [NodeIDSensor(node_id, proxy), *(S2PowerSensor(node_id, proxy, cq) for cq in proxy.provides_power_measurement_types)]
        async_add_entities(sensors)
        _LOGGER.debug("Restored %d sensor(s) for known node %s", len(sensors), node_id)


@callback
def _on_node_connected(node_id: str, rm_details: ResourceManagerDetails, *, hass: HomeAssistant, stored_nodes: dict, store: Store, registered_nodes: set, async_add_entities: AddEntitiesCallback) -> None:
    unique_types = list(dict.fromkeys(rm_details.provides_power_measurement_types))
    stored_nodes[node_id] = {
        "name": rm_details.name,
        "resource_id": str(rm_details.resource_id),
        "manufacturer": rm_details.manufacturer,
        "model": rm_details.model,
        "firmware_version": rm_details.firmware_version,
        "serial_number": rm_details.serial_number,
        "provides_power_measurement_types": [str(cq) for cq in unique_types],
    }
    hass.async_create_task(store.async_save(stored_nodes))
    if node_id in registered_nodes:
        return
    registered_nodes.add(node_id)
    sensors: list[SensorEntity] = [NodeIDSensor(node_id, rm_details), *(S2PowerSensor(node_id, rm_details, cq) for cq in unique_types)]
    async_add_entities(sensors)


def _new_constraint_sensors(node_id: str, rm_details, constraints: dict, seen_cqs: set, registered_keys: set, cls) -> list:
    sensors = []
    for cq in seen_cqs:
        for lt in ("upper", "lower"):
            key = (node_id, cq, lt)
            if key not in registered_keys:
                registered_keys.add(key)
                sensors.append(cls(node_id, rm_details, cq, lt, constraints))
    return sensors


@callback
def _on_power_constraints(node_id: str, rm_details, constraints: dict, *, registered_keys: set, async_add_entities: AddEntitiesCallback) -> None:
    if rm_details is None:
        return
    seen_cqs = {str(lr.commodity_quantity) for c in constraints.values() for lr in c.allowed_limit_ranges}
    new = _new_constraint_sensors(node_id, rm_details, constraints, seen_cqs, registered_keys, S2PowerConstraintSensor)
    if new:
        async_add_entities(new)


@callback
def _on_energy_constraints(node_id: str, rm_details, constraints: dict, *, registered_keys: set, async_add_entities: AddEntitiesCallback) -> None:
    if rm_details is None:
        return
    seen_cqs = {str(c.commodity_quantity) for c in constraints.values()}
    new = _new_constraint_sensors(node_id, rm_details, constraints, seen_cqs, registered_keys, S2EnergyConstraintSensor)
    if new:
        async_add_entities(new)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up S2 PEBC sensor entities from a config entry."""
    _store = Store(hass, _KNOWN_NODES_STORE_VERSION, _KNOWN_NODES_STORE_KEY)
    stored_nodes: dict[str, dict] = await _store.async_load() or {}
    registered_nodes: set[str] = set()
    registered_power_keys: set = set()
    registered_energy_keys: set = set()

    _restore_known_sensors(stored_nodes, registered_nodes, async_add_entities)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NODE_CONNECTED,
        lambda nid, rm: hass.loop.call_soon_threadsafe(
            lambda: _on_node_connected(nid, rm, hass=hass, stored_nodes=stored_nodes, store=_store, registered_nodes=registered_nodes, async_add_entities=async_add_entities)
        )))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_POWER_CONSTRAINTS,
        lambda nid, rm, c: hass.loop.call_soon_threadsafe(
            lambda: _on_power_constraints(nid, rm, c, registered_keys=registered_power_keys, async_add_entities=async_add_entities)
        )))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_ENERGY_CONSTRAINTS,
        lambda nid, rm, c: hass.loop.call_soon_threadsafe(
            lambda: _on_energy_constraints(nid, rm, c, registered_keys=registered_energy_keys, async_add_entities=async_add_entities)
        )))


class S2PowerSensor(RestoreSensor):
    """A power measurement sensor for one CommodityQuantity of an S2 Resource Manager.

    Examples of CommodityQuantity values (S2 spec):
      ELECTRIC.POWER.L1, ELECTRIC.POWER.L2, ELECTRIC.POWER.L3,
      ELECTRIC.POWER.3_PHASE_SYMMETRIC, HEAT.POWER, GAS.POWER, OIL.POWER
    Watts is used as the unit of measurement for power throughout. In line with S2 spec examples.
    see https://docs.s2standard.org/docs/examples/pv/#rm---cem-powermeasurement
    """

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    # Sensors start unavailable until the first PowerMeasurement arrives.
    _attr_available = False

    def __init__(self, node_id: str, rm_details: ResourceManagerDetails, commodity_quantity: str) -> None:
        self._node_id = str(node_id)
        self._commodity_quantity = commodity_quantity
        self._rm_details = rm_details

        self._attr_unique_id = f"{DOMAIN}_{node_id}_{commodity_quantity}"
        device_name = rm_details.name or str(rm_details.resource_id)
        self._attr_name = f"{device_name} {_format_commodity_quantity(commodity_quantity)}"
        self._attr_native_value: float | None = None
        self._attr_device_info = _device_info(self._node_id, rm_details)

    async def async_added_to_hass(self) -> None:
        """Subscribe to measurement updates and disconnect events once added to HA."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data and last_data.native_value is not None:
            self._attr_native_value = last_data.native_value
            self._attr_available = True
            self.async_write_ha_state()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_POWER_MEASUREMENT_FMT.format(node_id=self._node_id),
                self._handle_measurement,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_DISCONNECTED_FMT.format(node_id=self._node_id),
                self._handle_disconnected,
            )
        )

    @callback
    def _handle_measurement(
        self, commodity_quantity: str, value: float
    ) -> None:
        """Receive a PowerMeasurement update for one commodity quantity."""
        # str() normalises enum vs string mismatch for sensors restored from store.
        if str(commodity_quantity) != str(self._commodity_quantity):
            return  # not for this sensor
        self._attr_native_value = value
        self._attr_available = True
        self.async_write_ha_state()

    @callback
    def _handle_disconnected(self) -> None:
        """Mark unavailable on disconnect; native_value kept so RestoreSensor can restore it after restart."""
        self._attr_available = False
        self.async_write_ha_state()


class NodeIDSensor(SensorEntity):
    """Sensor that exposes the S2 node UUID.
    Shown on the device card so the user can copy it for sending PEBC instructions 
    using this integration's Send PEBC instruction HA service.
    """
    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:identifier"

    def __init__(self, node_id: str, rm_details: ResourceManagerDetails) -> None:
        self._node_id = str(node_id)
        self._rm_details = rm_details
        self._attr_unique_id = f"{DOMAIN}_{node_id}_node_id"
        self._attr_native_value = str(node_id)
        device_name = rm_details.name or str(rm_details.resource_id)
        self._attr_name = f"{device_name} Node ID"
        self._attr_device_info = _device_info(self._node_id, rm_details)


def _collect_limit_values(constraints: dict, commodity_quantity: str, target_type, is_upper: bool) -> list[float]:
    return [
        lr.range_boundary.end_of_range if is_upper else lr.range_boundary.start_of_range
        for c in constraints.values()
        for lr in c.allowed_limit_ranges
        if str(lr.commodity_quantity) == commodity_quantity and lr.limit_type == target_type
    ]


class S2PowerConstraintSensor(SensorEntity):
    """Most restrictive active PEBC.PowerConstraints limit (upper = min end_of_range, lower = max start_of_range)."""

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_available = False

    def __init__(
        self,
        node_id: str,
        rm_details: ResourceManagerDetails,
        commodity_quantity: str,
        limit_type: str,
        initial_constraints: dict,
    ) -> None:
        self._node_id = str(node_id)
        self._rm_details = rm_details
        self._commodity_quantity = commodity_quantity
        self._limit_type = limit_type
        self._attr_unique_id = f"{DOMAIN}_{node_id}_constraint_{commodity_quantity}_{limit_type}"
        device_name = rm_details.name or str(rm_details.resource_id)
        label = "Upper Limit" if limit_type == "upper" else "Lower Limit"
        self._attr_name = f"{device_name} {_format_commodity_quantity(commodity_quantity)} {label}"
        self._attr_native_value: float | None = None
        self._attr_device_info = _device_info(self._node_id, rm_details)
        self._update_value(initial_constraints)

    def _update_value(self, constraints: dict) -> None:
        from s2python.pebc import PEBCPowerEnvelopeLimitType
        is_upper = self._limit_type == "upper"
        target_type = PEBCPowerEnvelopeLimitType.UPPER_LIMIT if is_upper else PEBCPowerEnvelopeLimitType.LOWER_LIMIT
        values = _collect_limit_values(constraints, self._commodity_quantity, target_type, is_upper)
        if values:
            self._attr_native_value = min(values) if is_upper else max(values)
            self._attr_available = True
        else:
            self._attr_native_value = None
            self._attr_available = False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_POWER_CONSTRAINTS, self._handle_update)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_DISCONNECTED_FMT.format(node_id=self._node_id),
                self._handle_disconnected,
            )
        )

    @callback
    def _handle_update(self, node_id, _rm_details, constraints: dict) -> None:
        if str(node_id) != self._node_id:
            return
        self._update_value(constraints)
        self.async_write_ha_state()

    @callback
    def _handle_disconnected(self) -> None:
        self._attr_available = False
        self._attr_native_value = None
        self.async_write_ha_state()


class S2EnergyConstraintSensor(SensorEntity):
    """Most restrictive active PEBC.EnergyConstraint average power (upper_average_power / lower_average_power)."""

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_available = False

    def __init__(
        self,
        node_id: str,
        rm_details: ResourceManagerDetails,
        commodity_quantity: str,
        limit_type: str,
        initial_constraints: dict,
    ) -> None:
        self._node_id = str(node_id)
        self._rm_details = rm_details
        self._commodity_quantity = commodity_quantity
        self._limit_type = limit_type
        self._attr_unique_id = f"{DOMAIN}_{node_id}_energy_constraint_{commodity_quantity}_{limit_type}"
        device_name = rm_details.name or str(rm_details.resource_id)
        label = "Energy Upper Avg" if limit_type == "upper" else "Energy Lower Avg"
        self._attr_name = f"{device_name} {_format_commodity_quantity(commodity_quantity)} {label}"
        self._attr_native_value: float | None = None
        self._attr_device_info = _device_info(self._node_id, rm_details)
        self._update_value(initial_constraints)

    def _update_value(self, constraints: dict) -> None:
        values: list[float] = []
        for c in constraints.values():
            if str(c.commodity_quantity) != self._commodity_quantity:
                continue
            values.append(c.upper_average_power if self._limit_type == "upper" else c.lower_average_power)
        if values:
            self._attr_native_value = min(values) if self._limit_type == "upper" else max(values)
            self._attr_available = True
        else:
            self._attr_native_value = None
            self._attr_available = False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ENERGY_CONSTRAINTS, self._handle_update)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NODE_DISCONNECTED_FMT.format(node_id=self._node_id),
                self._handle_disconnected,
            )
        )

    @callback
    def _handle_update(self, node_id, _rm_details, constraints: dict) -> None:
        if str(node_id) != self._node_id:
            return
        self._update_value(constraints)
        self.async_write_ha_state()

    @callback
    def _handle_disconnected(self) -> None:
        self._attr_available = False
        self._attr_native_value = None
        self.async_write_ha_state()
