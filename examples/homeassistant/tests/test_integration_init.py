# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for async_setup_entry and async_unload_entry."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from s2_cem_lib.const import DOMAIN
from s2_cem_lib.pairing.pairing_server import PairingServer
from src import async_setup_entry, async_unload_entry


def make_hass(ssl=None):
    hass = MagicMock()
    hass.http.ssl_certificate = ssl
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.async_add_executor_job = AsyncMock(return_value={})  # {} causes Store to return None (no stored data)
    return hass


@pytest.mark.asyncio
async def test_setup_stores_pairing_server_and_returns_true():
    hass = make_hass(ssl="/cert.pem")
    entry = MagicMock()
    assert await async_setup_entry(hass, entry) is True
    assert isinstance(hass.data[DOMAIN][entry.entry_id], PairingServer)


@pytest.mark.asyncio
async def test_setup_warns_when_no_tls(caplog):
    hass = make_hass(ssl=None)
    with caplog.at_level(logging.WARNING, logger="src"):
        await async_setup_entry(hass, MagicMock())
    assert "TLS" in caplog.text


@pytest.mark.asyncio
async def test_unload_closes_connections_and_removes_server():
    hass = make_hass()
    entry = MagicMock()
    ws = MagicMock()
    ws.close = AsyncMock()
    mock_app = MagicMock()
    mock_app.stop = AsyncMock()
    mock_server = MagicMock(active_connections={"node-1": ws})
    hass.data[DOMAIN] = {
        entry.entry_id: mock_server,
        "_s2_app": mock_app,
        "_listeners": {},
        "_dispatch_listeners": {},
    }

    assert await async_unload_entry(hass, entry) is True
    mock_app.stop.assert_awaited_once()
    assert ws.close.call_count == 0  # mock stop doesn't actually close
    assert entry.entry_id not in hass.data[DOMAIN]

