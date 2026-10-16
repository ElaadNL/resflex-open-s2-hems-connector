# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for resource manager details handling."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from s2python.common import ResourceManagerDetails

from s2_cem_lib.websocket.handlers.resource_manager import handle_resource_manager_details


def _make_ws():
    ws = MagicMock()
    ws.close = AsyncMock()
    ws.send_str = AsyncMock()
    return ws


def _resource_manager_raw(control_types=None):
    return {
        "message_type": "ResourceManagerDetails",
        "message_id": str(uuid.uuid4()),
        "resource_id": str(uuid.uuid4()),
        "name": "My EV Charger",
        "roles": [{"role": "ENERGY_CONSUMER", "commodity": "ELECTRICITY"}],
        "manufacturer": "Acme",
        "model": "ChargeBox 3000",
        "serial_number": "SN-12345",
        "firmware_version": "1.2.3",
        "instruction_processing_delay": 500,
        "available_control_types": control_types or ["POWER_ENVELOPE_BASED_CONTROL"],
        "provides_forecast": False,
        "provides_power_measurement_types": ["ELECTRIC.POWER.L1"],
    }


@pytest.mark.asyncio
async def test_resource_manager_details_sends_reception_status_then_select_control_type():
    """Valid ResourceManagerDetails: ReceptionStatus OK is sent first, then SelectControlType."""
    ws = _make_ws()
    call_order = []
    with patch("s2_cem_lib.websocket.handlers.resource_manager.send_reception_status", new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("ack"))) as mock_ack, \
         patch("s2_cem_lib.websocket.handlers.resource_manager.send_select_control_type", new=AsyncMock(side_effect=lambda *a, **kw: call_order.append("sct"))) as mock_sct:
        # Inject event_bus so the handler can fire SIGNAL_NODE_CONNECTED
        from s2_cem_lib.websocket.node_state import get_state
        get_state("node-1").event_bus = MagicMock()
        rm_details = ResourceManagerDetails.from_dict(_resource_manager_raw())
        result = await handle_resource_manager_details(ws, "remote", "node-1", rm_details)
    mock_ack.assert_awaited_once()
    mock_sct.assert_awaited_once_with(ws, "node-1")
    assert call_order == ["ack", "sct"]
    assert result is True


@pytest.mark.asyncio
async def test_resource_manager_details_no_pebc_returns_early():
    """ResourceManagerDetails without PEBC returns early without sending SelectControlType."""
    ws = _make_ws()
    with patch("s2_cem_lib.websocket.handlers.resource_manager.send_select_control_type", new=AsyncMock()) as mock_sct:
        result = await handle_resource_manager_details(ws, "remote", "node-1", ResourceManagerDetails.from_dict(_resource_manager_raw(control_types=["NOT_CONTROLABLE"])))
    mock_sct.assert_not_called()
    assert result is True
