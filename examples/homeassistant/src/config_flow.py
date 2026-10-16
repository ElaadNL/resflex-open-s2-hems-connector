# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Config flow for S2 PEBC Integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util
from s2_cem_lib.const import DOMAIN, PAIRING_TOKEN_TIMEOUT_MINUTES, S2_PAIRING_PATH


class S2PEBCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for S2 PEBC Integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="S2 PEBC", data={})

        try:
            # get URL of this instance. we show this in pairing instructions for user
            this_url = get_url(self.hass, require_current_request=True)
        except (ValueError, RuntimeError):
            this_url = get_url(self.hass, allow_internal=True, allow_external=True)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "baseUrl": this_url,
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return S2PEBCOptionsFlow()


class S2PEBCOptionsFlow(OptionsFlow):
    """Show pairing info so a Resource Manager can be paired with this CEM."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})

        pairing_server = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if pairing_server is None:
            return self.async_abort(reason="not_loaded")

        try:
            base_url = get_url(self.hass, require_current_request=True)
        except (ValueError, RuntimeError):
            base_url = "https://homeassistant.local:8123"

        pairing_url = base_url + S2_PAIRING_PATH

        # Only generate a new token if there isn't already a valid (non-expired) one. 
        # Show the existing non-expired one to prevent issues with token deletion when users are trying to request pairing on multiple devices.
        if pairing_server.pairing_token is None or pairing_server.pairing_token.expired():
            pairing_info = pairing_server.generate_pairing_info(pairing_url)
            async_call_later(
                self.hass,
                PAIRING_TOKEN_TIMEOUT_MINUTES * 60,
                lambda _now: setattr(pairing_server, "pairing_token", None),
            )
        else:
            from s2_cem_lib.pairing.pairing_models import PairingInfo
            pairing_info = PairingInfo(
                pairingUrl=pairing_url,
                token=pairing_server.pairing_token.value,
                validUntil=pairing_server.pairing_token.valid_till,
            )

        return self.async_show_form(
            step_id="init",
            description_placeholders={
                "token": pairing_info.token,
                "baseUrl": base_url,
                "validUntil": dt_util.as_local(pairing_info.validUntil).strftime("%H:%M:%S"),
            },
        )
