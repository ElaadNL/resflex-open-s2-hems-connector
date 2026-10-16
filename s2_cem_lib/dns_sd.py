# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""DNS-SD advertiser for LAN discovery.

Publishes _s2connect._tcp with _cem subtype
"""
from __future__ import annotations

import logging

try:
    from zeroconf import ServiceInfo, Zeroconf
    _HAS_ZEROCONF = True
except ImportError:
    _HAS_ZEROCONF = False

_LOGGER = logging.getLogger(__name__)

_SERVICE_TYPE = "_s2connect._tcp.local."
_SUB_TYPE = "_cem._sub." + _SERVICE_TYPE


class S2DnsSdAdvertiser:
    """Registers an S2 Connect CEM service via mDNS / DNS-SD."""

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        pairing_url: str,
        longpolling_url: str | None = None,
        endpoint_name: str | None = None,
        endpoint_logo_url: str | None = None,
        ip_address: str | None = None,
        zc: Zeroconf | None = None,
    ) -> None:
        if not _HAS_ZEROCONF:
            _LOGGER.warning("zeroconf unavailable - DNS-SD disabled")
            self._zc = None
            self._own_zc = False
            self._info = None
            self._sub_info = None
            return

        self._zc = zc
        self._own_zc = zc is None
        self._info: ServiceInfo | None = None
        self._sub_info: ServiceInfo | None = None

        # The spec requires the base URLs to include a trailing slash.
        if not pairing_url.endswith("/"):
            pairing_url += "/"
        if longpolling_url and not longpolling_url.endswith("/"):
            longpolling_url += "/"

        properties: dict[str, str] = {
            "txtvers": "1",
            "pairingUrl": pairing_url,
        }
        if longpolling_url:
            properties["longpollingUrl"] = longpolling_url
        if endpoint_name:
            properties["e_name"] = endpoint_name
        if endpoint_logo_url:
            properties["e_logoUrl"] = endpoint_logo_url

        instance_name = f"{hostname}.{_SERVICE_TYPE}"

        import socket as _socket
        addrs = []
        if ip_address:
            try:
                addrs.append(_socket.inet_aton(ip_address))
            except OSError:
                pass

        self._info = ServiceInfo(
            _SERVICE_TYPE,
            instance_name,
            addresses=addrs,
            port=port,
            properties=properties,
            server=f"{hostname}.local.",
        )

        self._sub_info = ServiceInfo(
            _SUB_TYPE,
            f"{hostname}:cem.{_SERVICE_TYPE}",
            addresses=addrs,
            port=port,
            properties=properties,
            server=f"{hostname}.local.",
        )

    async def register(self) -> None:
        """Start advertising the S2 Connect service."""
        if not _HAS_ZEROCONF or self._info is None:
            return
        if self._zc is None:
            self._zc = Zeroconf()

        _LOGGER.info(
            "Registering DNS-SD service %s on port %s (subtype %s)",
            _SERVICE_TYPE,
            self._info.port,
            _SUB_TYPE,
        )
        await self._zc.async_register_service(self._info, allow_name_change=True)
        await self._zc.async_register_service(self._sub_info, allow_name_change=True)

    async def unregister(self) -> None:
        """Stop advertising."""
        if self._zc is None:
            return
        if self._sub_info is not None:
            await self._zc.async_unregister_service(self._sub_info)
        if self._info is not None:
            await self._zc.async_unregister_service(self._info)
        if self._own_zc:
            await self._zc._async_close()
