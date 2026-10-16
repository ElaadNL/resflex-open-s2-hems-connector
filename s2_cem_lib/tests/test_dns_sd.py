# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for DNS-SD advertiser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from s2_cem_lib.dns_sd import S2DnsSdAdvertiser


def _mock_zc():
    zc = MagicMock()
    zc.async_register_service = AsyncMock()
    zc.async_unregister_service = AsyncMock()
    zc._async_close = AsyncMock()
    return zc


# without zeroconf tests ---------------------------------------------------------------

def test_init_without_zeroconf():
    with patch("s2_cem_lib.dns_sd._HAS_ZEROCONF", False):
        a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/")
    assert a._zc is None
    assert a._info is None


@pytest.mark.asyncio
async def test_register_without_zeroconf():
    with patch("s2_cem_lib.dns_sd._HAS_ZEROCONF", False):
        a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/")
        await a.register()


@pytest.mark.asyncio
async def test_unregister_without_zeroconf():
    with patch("s2_cem_lib.dns_sd._HAS_ZEROCONF", False):
        a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/")
        await a.unregister()


# with zeroconf tests ---------------------------------------------------------------

def test_init_creates_service_info():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    assert a._info is not None
    assert a._sub_info is not None


def test_lazy_zeroconf():
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/")
    assert a._zc is None
    assert a._own_zc is True


def test_ip_address_in_record():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", ip_address="192.168.0.1", zc=zc)
    assert len(a._info.addresses) == 1


def test_no_ip_address_empty():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    assert len(a._info.addresses) == 0


def test_endpoint_name_in_properties():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", endpoint_name="My CEM", zc=zc)
    assert a._info.properties[b"e_name"] == b"My CEM"


def test_pairing_url_in_properties():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/pair", zc=zc)
    assert a._info.properties[b"pairingUrl"] == b"https://h/pair/"


def test_pairing_url_trailing_slash_already_present():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/pair/", zc=zc)
    assert a._info.properties[b"pairingUrl"] == b"https://h/pair/"


def test_longpolling_url_in_properties():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(
        hostname="h",
        port=8443,
        pairing_url="https://h/pair",
        longpolling_url="https://h/pair",
        zc=zc,
    )
    assert a._info.properties[b"longpollingUrl"] == b"https://h/pair/"
    assert a._sub_info.properties[b"longpollingUrl"] == b"https://h/pair/"


def test_no_longpolling_url_when_not_provided():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/pair", zc=zc)
    assert b"longpollingUrl" not in a._info.properties


@pytest.mark.asyncio
async def test_register_calls_zeroconf():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    await a.register()
    assert zc.async_register_service.call_count == 2


@pytest.mark.asyncio
async def test_unregister_calls_zeroconf():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    await a.unregister()
    assert zc.async_unregister_service.call_count == 2


@pytest.mark.asyncio
async def test_unregister_closes_own_zeroconf():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    a._own_zc = True
    await a.unregister()
    zc._async_close.assert_called_once()


@pytest.mark.asyncio
async def test_unregister_keeps_shared_zeroconf():
    zc = _mock_zc()
    a = S2DnsSdAdvertiser(hostname="h", port=8443, pairing_url="https://h/", zc=zc)
    await a.unregister()
    zc._async_close.assert_not_called()
