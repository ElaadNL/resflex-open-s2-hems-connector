# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for custom Home Assistant services"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from s2_cem_lib.const import DOMAIN
from s2_cem_lib.pairing.pairing_server import PairingServer
from s2_cem_lib.websocket.node_state import PowerEnvelopeSpec
from s2python.common import CommodityQuantity
from s2python.pebc import PEBCPowerEnvelopeLimitType
from src.services import (
    _validate_envelopes_against_constraints,
    handle_initiate_pairing,
    handle_revoke_instruction,
    handle_send_instruction,
    handle_unpair_node,
)


def make_hass(ssl=None):
    hass = MagicMock()
    hass.http.ssl_certificate = ssl
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass

@pytest.mark.asyncio
async def test_handle_unpair_node_removes_node_and_closes_ws():
    hass = make_hass()
    server = MagicMock(spec=PairingServer)
    node_id = uuid.uuid4()
    ws = AsyncMock()
    server.active_connections = {node_id: ws}
    server.paired_nodes = {node_id: MagicMock()}
    server.unpair_node = MagicMock(return_value=ws)
    hass.data[DOMAIN] = {"e1": server}
    call = MagicMock()
    call.data = {"node_id": str(node_id)}
    with patch("src.services._notify_and_close", new=AsyncMock()) as mock_notify:
        await handle_unpair_node(hass, call)
    server.unpair_node.assert_called_once_with(node_id)
    mock_notify.assert_called_once()


def test_handle_initiate_pairing():
    hass = make_hass()
    node_id = uuid.uuid4()
    server = MagicMock()
    server.active_connections = {}
    server.initiate_pairing = MagicMock(return_value=True)
    hass.data[DOMAIN] = {"e1": server}
    call = MagicMock()
    call.data = {"node_id": str(node_id), "pairing_token": "ABC123"}
    handle_initiate_pairing(hass, call)
    server.initiate_pairing.assert_called_once_with(node_id, pairing_token="ABC123")


def test_handle_initiate_pairing_not_polling_raises():
    hass = make_hass()
    node_id = uuid.uuid4()
    server = MagicMock()
    server.active_connections = {}
    server.initiate_pairing = MagicMock(return_value=False)
    hass.data[DOMAIN] = {"e1": server}
    call = MagicMock()
    call.data = {"node_id": str(node_id), "pairing_token": "ABC123"}
    with pytest.raises(ServiceValidationError):
        handle_initiate_pairing(hass, call)


@pytest.mark.asyncio
async def test_handle_revoke_instruction_calls_send_revoke():
    hass = make_hass()
    node_id = uuid.uuid4()
    instruction_id = uuid.uuid4()
    ws = AsyncMock()
    server = MagicMock()
    server.active_connections = {node_id: ws}
    hass.data[DOMAIN] = {"e1": server}
    call = MagicMock()
    call.data = {"node_id": str(node_id), "instruction_id": str(instruction_id)}
    with patch("src.services.send_revoke_instruction", new=AsyncMock()) as mock_revoke:
        await handle_revoke_instruction(hass, call)
    mock_revoke.assert_called_once_with(ws, str(node_id), instruction_id)


@pytest.mark.asyncio
async def test_handle_send_instruction():
    hass = make_hass()
    node_id = uuid.uuid4()
    ws = AsyncMock()
    server = MagicMock()
    server.active_connections = {node_id: ws}
    hass.data[DOMAIN] = {"e1": server}
    call = MagicMock()
    call.data = {
        "node_id": str(node_id),
        "duration": 3600000,
        "power_envelopes": [{"commodity_quantity": "ELECTRIC.POWER.L1", "upper_limit": 1000.0, "lower_limit": 0.0}],
    }
    constraint = MagicMock()
    limit_range = MagicMock()
    limit_range.commodity_quantity = CommodityQuantity.ELECTRIC_POWER_L1
    constraint.allowed_limit_ranges = [limit_range]
    mock_state = MagicMock()
    mock_state.power_constraints = {"c1": constraint}
    with patch("src.services.get_state", return_value=mock_state), \
         patch("src.services.send_pebc_instruction", new=AsyncMock()) as mock_send:
        await handle_send_instruction(hass, call)
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_handle_send_instruction_accepts_quantity_from_second_and_third_constraints():
    """L2 + L3 quantities are advertised in separate PowerConstraints (one per phase).
    The advertised set must be collected across all stored constraints, not just the first."""
    hass = make_hass()
    node_id = uuid.uuid4()
    ws = AsyncMock()
    server = MagicMock()
    server.active_connections = {node_id: ws}
    hass.data[DOMAIN] = {"e1": server}

    def make_constraint(cq):
        lr = MagicMock()
        lr.commodity_quantity = cq
        c = MagicMock()
        c.allowed_limit_ranges = [lr]
        return c

    mock_state = MagicMock()
    mock_state.power_constraints = {
        "c1": make_constraint(CommodityQuantity.ELECTRIC_POWER_L1),
        "c2": make_constraint(CommodityQuantity.ELECTRIC_POWER_L2),
        "c3": make_constraint(CommodityQuantity.ELECTRIC_POWER_L3),
    }

    call = MagicMock()
    call.data = {
        "node_id": str(node_id),
        "duration": 3600000,
        "power_envelopes": [
            {"commodity_quantity": "ELECTRIC.POWER.L2", "upper_limit": 1000.0, "lower_limit": 0.0},
            {"commodity_quantity": "ELECTRIC.POWER.L3", "upper_limit": 1000.0, "lower_limit": 0.0},
        ],
    }

    with patch("src.services.get_state", return_value=mock_state), \
         patch("src.services._validate_envelopes_against_constraints"), \
         patch("src.services.send_pebc_instruction", new=AsyncMock()) as mock_send:
        await handle_send_instruction(hass, call)

    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_handle_send_instruction_rejects_duplicate_commodity_quantity():
    hass = make_hass()
    node_id = uuid.uuid4()
    call = MagicMock()
    call.data = {
        "node_id": str(node_id),
        "duration": 3600000,
        "power_envelopes": [
            {"commodity_quantity": "ELECTRIC.POWER.L1", "upper_limit": 1000.0, "lower_limit": 0.0},
            {"commodity_quantity": "ELECTRIC.POWER.L1", "upper_limit": 500.0, "lower_limit": 0.0},
        ],
    }
    with pytest.raises(ServiceValidationError, match="Duplicate commodity_quantity"):
        await handle_send_instruction(hass, call)


# Abnormal condition tests

def _make_ranges(cq):
    def r(start, end, abn):
        m = MagicMock()
        m.commodity_quantity = cq
        m.limit_type = PEBCPowerEnvelopeLimitType.UPPER_LIMIT
        m.abnormal_condition_only = abn
        m.range_boundary = MagicMock(start_of_range=start, end_of_range=end)
        return m
    constraint = MagicMock()
    constraint.allowed_limit_ranges = [r(0, 3680, False), r(3680, 4000, True)]
    return constraint


def test_normal_instruction_rejects_value_in_abnormal_only_range():
    # 3900 fits the abnormal-only range but not the base range. must be rejected for normal instruction
    cq = CommodityQuantity.ELECTRIC_POWER_L1
    specs = [PowerEnvelopeSpec(commodity_quantity=cq, upper_limit=3900.0, lower_limit=0.0)]
    with pytest.raises(ServiceValidationError, match="allowed range"):
        _validate_envelopes_against_constraints(specs, [_make_ranges(cq)], False)


def test_abnormal_instruction_accepts_value_in_abnormal_only_range():
    # 3900 is outside base range [0,3680] but within abnormal range [3680,4000]. must be accepted
    cq = CommodityQuantity.ELECTRIC_POWER_L1
    specs = [PowerEnvelopeSpec(commodity_quantity=cq, upper_limit=3900.0, lower_limit=0.0)]
    _validate_envelopes_against_constraints(specs, [_make_ranges(cq)], True)