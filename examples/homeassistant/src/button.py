# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2 PEBC button platform.

Implements CEM initiated unpairing of device. This button entity is created for each RM connected.
Pressing the button triggers the unpair.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from s2_cem_lib.const import DOMAIN, SIGNAL_NODE_CONNECTED
from s2python.common import ResourceManagerDetails

_LOGGER = logging.getLogger(__name__)

# Home Assistant requires this function to be async, even though it doesn't do any async work itself.
# HA awaits internally
async def async_setup_entry( # NOSONAR
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register a listener that creates an Unpair button whenever a new RM connects"""
    registered_nodes: set[str] = set()

    @callback
    def _handle_node_connected(node_id: str, rm_details: ResourceManagerDetails) -> None:
        if node_id in registered_nodes:
            _LOGGER.debug("Button entity for node %s already registered, skipping", node_id)
            return
        registered_nodes.add(node_id)
        async_add_entities([UnpairButton(node_id, rm_details)])
        _LOGGER.debug("Created Unpair button for node %s", node_id)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NODE_CONNECTED, _handle_node_connected)
    )


class UnpairButton(ButtonEntity):
    """Button that triggers CEM initiated unpairing of a RM
    Triggers deletion of all node info, sends RECONNECT SessionRequest to RM and closes WS connection.
    """

    _attr_icon = "mdi:link-off"

    def __init__(self, node_id: str, rm_details: ResourceManagerDetails) -> None:
        self._node_id = str(node_id)
        self._rm_details = rm_details
        device_name = rm_details.name or str(rm_details.resource_id)
        self._attr_unique_id = f"{DOMAIN}_{node_id}_unpair"
        self._attr_name = f"Unpair {device_name}"

    @property
    def device_info(self) -> DeviceInfo:
        """Groups the button under the same device as the power sensors."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._node_id)},
            name=self._rm_details.name or str(self._rm_details.resource_id),
            manufacturer=self._rm_details.manufacturer,
            model=self._rm_details.model,
            sw_version=self._rm_details.firmware_version,
        )

    async def async_press(self) -> None:
        """Unpair this RM from the CEM."""
        await self.hass.services.async_call(
            DOMAIN,
            "unpair_node",
            {"node_id": self._node_id},
        )
