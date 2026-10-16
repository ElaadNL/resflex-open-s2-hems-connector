# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2 PEBC Integration."""
from __future__ import annotations

import hashlib
import logging
import uuid
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.components.zeroconf import async_get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.network import get_url
from homeassistant.helpers.storage import Store
from OpenSSL import crypto
from s2_cem_lib.const import DOMAIN, S2_PAIRING_PATH, S2_SUPPORTED_VERSIONS, SIGNAL_RM_LOG
from s2_cem_lib.dns_sd import S2DnsSdAdvertiser
from s2_cem_lib.pairing.pairing_models import Deployment, EndpointDescription, Role, S2NodeDescription
from s2_cem_lib.s2_cem.app import S2CemApp

from .s2_cem.ha_adapters import HAEventBus, HAHostEnv, HAPersistence, HATaskScheduler
from .services import handle_initiate_pairing, handle_revoke_instruction, handle_send_instruction, handle_unpair_node

_LOGGER = logging.getLogger(__name__)

_SERVICE_UNPAIR_NODE = "unpair_node"
_SERVICE_SCHEMA_UNPAIR_NODE = vol.Schema({vol.Required("node_id"): str})

_SERVICE_INITIATE_PAIRING = "initiate_pairing"
_SERVICE_SCHEMA_INITIATE_PAIRING = vol.Schema({
    vol.Required("node_id"): str,
    vol.Required("pairing_token"): str,
})

_SERVICE_REVOKE_INSTRUCTION = "revoke_instruction"
_SERVICE_SCHEMA_REVOKE_INSTRUCTION = vol.Schema({
    vol.Required("node_id"): str,
    vol.Optional("instruction_id"): str,
})

_SERVICE_SEND_INSTRUCTION = "send_instruction"
_SERVICE_SCHEMA_SEND_INSTRUCTION = vol.Schema({
    vol.Required("node_id"): str,
    vol.Required("power_envelopes"): [
        vol.Schema({
            vol.Required("commodity_quantity"): str,
            vol.Required("upper_limit"): vol.Coerce(float),
            vol.Required("lower_limit"): vol.Coerce(float),
        })
    ],
    vol.Optional("duration", default=3_600_000): vol.Coerce(int),
    vol.Optional("abnormal_condition", default=False): bool,
    vol.Optional("execution_time"): str,
})

_NODE_DESCRIPTION = S2NodeDescription(
    id=uuid.uuid4(),
    brand="Home Assistant",
    type="CEM",
    modelName="S2 PEBC Integration",
    role=Role.CEM,
)

_ENDPOINT_DESCRIPTION = EndpointDescription(deployment=Deployment.LAN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up S2 PEBC Integration from a config entry."""
    certificate_fingerprint: bytes | None = None
    if hass.http.ssl_certificate:
        certificate_fingerprint = await hass.async_add_executor_job(configure_certificate_fingerprint, hass)
    else:
        _LOGGER.warning("S2 PEBC: HA is not configured with TLS. The S2 spec requires WSS. Add ssl_certificate / ssl_key to configuration.yaml.")

    app = S2CemApp(
        _NODE_DESCRIPTION,
        persistence=HAPersistence(hass, f"{DOMAIN}_paired_nodes"),
        event_bus=HAEventBus(hass),
        host_env=HAHostEnv(hass),
        scheduler=HATaskScheduler(hass),
        endpoint_description=_ENDPOINT_DESCRIPTION,
        supported_s2_message_versions=S2_SUPPORTED_VERSIONS,
        certificate_fingerprint=certificate_fingerprint,
        dns_sd_advertiser=await _build_dns_sd_advertiser(hass, _ENDPOINT_DESCRIPTION),
    )
    await app.start()
    app.register_routes(hass.http.app)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = app.pairing_server

    # Bridge engine RM log signals to the HA event bus for user automations.
    def _bridge_rm_log(node_id, level, message, source, timestamp):
        hass.loop.call_soon_threadsafe(
            hass.bus.async_fire,
            "s2_pebc_rm_log",
            {
                "node_id": str(node_id),
                "level": level,
                "message": message,
                "source": source,
                "timestamp": timestamp,
            },
        )
    unsub_dispatch = async_dispatcher_connect(hass, SIGNAL_RM_LOG, _bridge_rm_log)

    def _s2_rm_log_listener(event):
        try:
            node = event.data.get("node_id")
            level = event.data.get("level")
            msg = event.data.get("message")
            _LOGGER.info("s2_pebc_rm_log event - node=%s level=%s message=%s", node, level, msg)
        except Exception:
            _LOGGER.exception("Error handling s2_pebc_rm_log event")
    unsub_event = hass.bus.async_listen("s2_pebc_rm_log", _s2_rm_log_listener)

    hass.data[DOMAIN].setdefault("_listeners", {})[entry.entry_id] = unsub_event
    hass.data[DOMAIN].setdefault("_dispatch_listeners", {})[entry.entry_id] = unsub_dispatch
    hass.data[DOMAIN]["_s2_app"] = app

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button"])
    if not hass.services.has_service(DOMAIN, _SERVICE_UNPAIR_NODE):
        async def _unpair_handler(call: ServiceCall) -> None:
            await handle_unpair_node(hass, call)
        hass.services.async_register(DOMAIN, _SERVICE_UNPAIR_NODE, _unpair_handler, schema=_SERVICE_SCHEMA_UNPAIR_NODE)
    if not hass.services.has_service(DOMAIN, _SERVICE_INITIATE_PAIRING):
        def _initiate_pairing_handler(call: ServiceCall) -> None:
            handle_initiate_pairing(hass, call)
        hass.services.async_register(DOMAIN, _SERVICE_INITIATE_PAIRING, _initiate_pairing_handler, schema=_SERVICE_SCHEMA_INITIATE_PAIRING)
    if not hass.services.has_service(DOMAIN, _SERVICE_SEND_INSTRUCTION):
        async def _send_instruction_handler(call: ServiceCall) -> None:
            await handle_send_instruction(hass, call)
        hass.services.async_register(DOMAIN, _SERVICE_SEND_INSTRUCTION, _send_instruction_handler, schema=_SERVICE_SCHEMA_SEND_INSTRUCTION)
    if not hass.services.has_service(DOMAIN, _SERVICE_REVOKE_INSTRUCTION):
        async def _revoke_instruction_handler(call: ServiceCall) -> None:
            await handle_revoke_instruction(hass, call)
        hass.services.async_register(DOMAIN, _SERVICE_REVOKE_INSTRUCTION, _revoke_instruction_handler, schema=_SERVICE_SCHEMA_REVOKE_INSTRUCTION)
    _LOGGER.info("S2 PEBC integration started")
    return True


async def _build_dns_sd_advertiser(hass: HomeAssistant, endpoint: EndpointDescription) -> S2DnsSdAdvertiser | None:
    """Build a DNS-SD advertiser for LAN discovery, or None for WAN."""
    if endpoint.deployment != Deployment.LAN:
        return None
    try:
        base_url = get_url(hass=hass, require_ssl=True)
        parsed = urlparse(base_url)
        hostname = parsed.hostname or "homeassistant"
        port = parsed.port or 443
        pairing_url = f"{base_url.rstrip('/')}{S2_PAIRING_PATH}"
        zc = await async_get_instance(hass)
        return S2DnsSdAdvertiser(
            hostname=hostname,
            port=port,
            pairing_url=pairing_url,
            longpolling_url=pairing_url,
            zc=zc,
        )
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("S2 PEBC: unable to build DNS-SD advertiser: %s (configure your HA URL in Settings -> System -> Network)", e)
        return None


def configure_certificate_fingerprint(hass: HomeAssistant) -> bytes | None:
    """Extract the SHA256 fingerprint of the TLS leaf certificate.

    Returns the raw 32-byte SHA256 hash, or None on failure.
    """
    try:
        with open(hass.http.ssl_certificate, "rb") as file:
            pem_certificate = crypto.load_certificate(crypto.FILETYPE_PEM, file.read())
            der_certificate = crypto.dump_certificate(crypto.FILETYPE_ASN1, pem_certificate)
            return hashlib.sha256(der_certificate).digest()
    except (OSError, ValueError) as e:
        _LOGGER.warning("S2 PEBC unable to load certificate: %s", e)
        return None

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. closes all active WebSocket connections."""
    app: S2CemApp | None = hass.data[DOMAIN].pop("_s2_app", None)
    hass.data[DOMAIN].pop(entry.entry_id, None)

    listeners = hass.data[DOMAIN].get("_listeners", {})
    unsub = listeners.pop(entry.entry_id, None)
    if unsub:
        try:
            unsub()
        except Exception:
            _LOGGER.exception("Failed to unsubscribe s2_pebc_rm_log listener")
    dispatch_listeners = hass.data[DOMAIN].get("_dispatch_listeners", {})
    unsub_dispatch = dispatch_listeners.pop(entry.entry_id, None)
    if unsub_dispatch:
        try:
            unsub_dispatch()
        except Exception:
            _LOGGER.exception("Failed to unsubscribe RM log dispatch listener")

    if app is not None:
        await app.stop()

    await hass.config_entries.async_unload_platforms(entry, ["sensor", "button"])

    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, _SERVICE_UNPAIR_NODE)
        hass.services.async_remove(DOMAIN, _SERVICE_SEND_INSTRUCTION)
        hass.services.async_remove(DOMAIN, _SERVICE_REVOKE_INSTRUCTION)
        hass.services.async_remove(DOMAIN, _SERVICE_INITIATE_PAIRING)

    return True


async def async_remove_entry(hass: HomeAssistant, _entry: ConfigEntry) -> None:
    """Clear all stored data when the integration is removed."""
    _LOGGER.info("S2 PEBC: removing integration, clearing all stored data")
    paired_persistence = HAPersistence(hass, f"{DOMAIN}_paired_nodes")
    await paired_persistence.remove()
    sensor_store = Store(hass, 1, f"{DOMAIN}_known_nodes")
    await sensor_store.async_remove()