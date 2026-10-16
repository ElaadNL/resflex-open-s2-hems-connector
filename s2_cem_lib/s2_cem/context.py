# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Shared context object stored on ``request.app["s2_ctx"]``.

Routes and views read everything they need from here.
"""

from __future__ import annotations

from .ports import EventBus, HostEnv, Persistence, TaskScheduler


class S2Context:
    """Bundle that the S2CemApp wires together and attaches to the aiohttp app.

    Every view reads ``request.app["s2_ctx"]`` and pulls out the piece
    it needs: the pairing server, a persistence handle, a scheduler, etc.
    """

    __slots__ = ("event_bus", "host_env", "pairing_server", "persistence", "scheduler")

    def __init__(
        self,
        pairing_server,
        *,
        host_env: HostEnv,
        scheduler: TaskScheduler,
        persistence: Persistence,
        event_bus: EventBus,
    ) -> None:
        self.pairing_server = pairing_server
        self.host_env = host_env
        self.scheduler = scheduler
        self.persistence = persistence
        self.event_bus = event_bus
