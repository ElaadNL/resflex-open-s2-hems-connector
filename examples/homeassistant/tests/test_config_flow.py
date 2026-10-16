# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for S2PEBCConfigFlow and S2PEBCOptionsFlow."""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from s2_cem_lib.const import DOMAIN, S2_PAIRING_PATH
from s2_cem_lib.pairing.pairing_models import PairingInfo
from src.config_flow import S2PEBCConfigFlow, S2PEBCOptionsFlow

TEST_BASE_URL = "https://homeassistant.local:8123"
TEST_TOKEN = "ABC123"
TEST_VALID_UNTIL = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)


@pytest.mark.asyncio
async def test_config_flow_shows_form():
    """async_step_user shows a form with the pairing URL."""
    flow = S2PEBCConfigFlow()
    flow.hass = MagicMock()
    flow._async_current_entries = MagicMock(return_value=[])

    with patch("src.config_flow.get_url", return_value=TEST_BASE_URL):
        result = await flow.async_step_user()

    assert result["type"] == "form"
    assert result["description_placeholders"]["baseUrl"] == TEST_BASE_URL


@pytest.mark.asyncio
async def test_options_flow_shows_token():
    """async_step_init generates a token and shows it with pairing/ws URLs."""
    server = MagicMock()
    server.pairing_token = None
    server.generate_pairing_info.return_value = PairingInfo(
        pairingUrl=TEST_BASE_URL + S2_PAIRING_PATH,
        token=TEST_TOKEN,
        validUntil=TEST_VALID_UNTIL,
    )

    flow = S2PEBCOptionsFlow()
    flow.handler = "entry-1"
    mock_entry = MagicMock()
    mock_entry.entry_id = "entry-1"
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = mock_entry
    flow.hass.data = {DOMAIN: {"entry-1": server}}

    with patch("src.config_flow.get_url", return_value=TEST_BASE_URL), \
         patch("src.config_flow.async_call_later"):
        result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["description_placeholders"]["token"] == TEST_TOKEN
    assert result["description_placeholders"]["baseUrl"] == TEST_BASE_URL
