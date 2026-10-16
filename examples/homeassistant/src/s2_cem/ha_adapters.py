# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Home Assistant implementations of the s2_cem ports.

Drop these into S2CemApp and the engine runs on HA.
Replace with your own classes for a different HEMS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store


class HAEventBus:
    """Wraps HA's dispatcher so the engine does not import HA directly."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def send(self, signal: str, *args: Any) -> None:
        async_dispatcher_send(self._hass, signal, *args)


class HAPersistence:
    """Wraps HA's Store so paired node data survives restarts."""

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        self._store = Store(hass, 1, key)

    async def load(self) -> dict | None:
        return await self._store.async_load()

    async def save(self, data: dict) -> None:
        await self._store.async_save(data)

    async def remove(self) -> None:
        await self._store.async_remove()


class HAHostEnv:
    """Tells the engine where HA is running and lets it show notifications."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def notify(self, notification_id: str, title: str, message: str) -> None:
        async_create(self._hass, message=message, title=title, notification_id=notification_id)

    def dismiss(self, notification_id: str) -> None:
        async_dismiss(self._hass, notification_id=notification_id)


class HATaskScheduler:
    """Wraps HA's task runner so the engine never touches the event loop directly."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def create_task(self, coro) -> None:
        self._hass.async_create_task(coro)

    def call_later(self, delay_seconds: float, cb: Callable[[], None]) -> Callable[[], None]:
        return async_call_later(self._hass, delay_seconds, lambda _now: cb())
