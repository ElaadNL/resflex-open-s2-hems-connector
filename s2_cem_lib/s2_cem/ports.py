# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""What a HEMS needs to implement to use the S2 CEM engine.

Four interfaces. Write a class for each one for your platform and
pass them into S2CemApp.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class EventBus(Protocol):
    """Publish telemetry and state changes."""

    def send(self, signal: str, *args: Any) -> None: ...


class Persistence(Protocol):
    """Key-value storage for paired node data (plain dicts)."""

    async def load(self) -> dict | None: ...
    async def save(self, data: dict) -> None: ...
    async def remove(self) -> None: ...


class HostEnv(Protocol):
    """Host environment: notifications."""

    def notify(self, notification_id: str, title: str, message: str) -> None: ...
    def dismiss(self, notification_id: str) -> None: ...


class TaskScheduler(Protocol):
    """Async task spawning and delayed callbacks."""

    def create_task(self, coro: Awaitable[None]) -> None: ...
    def call_later(self, delay_seconds: float, cb: Callable[[], None]) -> Callable[[], None]: ...
