# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""S2CemApp is the single entry point for the S2 CEM engine.

Give it your four adapter classes and a node description, call
start() then register_routes().  The engine handles the rest.
"""

from __future__ import annotations

import logging

from aiohttp import web

from ..connection.views import get_views as get_connection_views
from ..const import S2_WS_PATH
from ..dns_sd import S2DnsSdAdvertiser
from ..pairing.pairing_models import CommunicationProtocol, EndpointDescription, S2NodeDescription
from ..pairing.pairing_server import PairingServer
from ..pairing.persistence import restore_paired_nodes
from ..pairing.views import get_views as get_pairing_views
from ..websocket.node_state import _registry as _node_registry
from ..websocket.ws_view import S2WebSocketView
from .context import S2Context
from .ports import EventBus, HostEnv, Persistence, TaskScheduler

_LOGGER = logging.getLogger(__name__)


class S2CemApp:
    """Wires pairing, WS, and PEBC state together via the four ports.

    Create one instance per HEMS deployment.  Call start() then
    register_routes(), and stop() on shutdown.
    """

    def __init__(
        self,
        node_description: S2NodeDescription,
        *,
        persistence: Persistence,
        event_bus: EventBus,
        host_env: HostEnv,
        scheduler: TaskScheduler,
        endpoint_description: EndpointDescription | None = None,
        supported_communication_protocols: list[CommunicationProtocol] | None = None,
        supported_s2_message_versions: list[str] | None = None,
        certificate_fingerprint: bytes | None = None,
        dns_sd_advertiser: S2DnsSdAdvertiser | None = None,
        pairing_token: str | None = None,
    ) -> None:
        self._node_description = node_description
        self._persistence = persistence
        self._event_bus = event_bus
        self._host_env = host_env
        self._scheduler = scheduler
        self._endpoint_description = endpoint_description
        self._supported_communication_protocols = supported_communication_protocols
        self._supported_s2_message_versions = supported_s2_message_versions
        self._certificate_fingerprint = certificate_fingerprint
        self._dns_sd_advertiser = dns_sd_advertiser
        self._pairing_token = pairing_token

        self._pairing_server: PairingServer | None = None
        self._ctx: S2Context | None = None

    # -- public API -------------------------------------------------------

    async def start(self) -> None:
        """Create pairing server, restore paired nodes, wire context."""
        _LOGGER.info("S2CemApp starting")

        self._pairing_server = PairingServer(
            self._node_description.role, self._node_description,
            endpoint_description=self._endpoint_description,
            supported_communication_protocols=self._supported_communication_protocols,
            supported_s2_message_versions=self._supported_s2_message_versions,
            certificate_fingerprint=self._certificate_fingerprint,
            pairing_token=self._pairing_token,
        )
        await restore_paired_nodes(self._persistence, self._pairing_server)

        self._ctx = S2Context(
            self._pairing_server,
            host_env=self._host_env,
            scheduler=self._scheduler,
            persistence=self._persistence,
            event_bus=self._event_bus,
        )

        if self._dns_sd_advertiser is not None:
            await self._dns_sd_advertiser.register()

    async def stop(self) -> None:
        """Close active WS connections and tear down."""
        _LOGGER.info("S2CemApp stopping")
        if self._dns_sd_advertiser is not None:
            await self._dns_sd_advertiser.unregister()
        if self._pairing_server is not None:
            for node_id, ws in tuple(self._pairing_server.active_connections.items()):
                await ws.close(message=b"Engine stopped")
                _LOGGER.info("Closed S2 WebSocket for node %s", node_id)
            self._pairing_server.active_connections.clear()
            self._pairing_server = None
        _node_registry.clear()
        self._ctx = None

    def register_routes(self, app: web.Application) -> None:
        """Attach the WS, pairing, and connection views to *app*."""
        ctx = self._ctx
        if ctx is None:
            raise RuntimeError("Call start() before register_routes()")
        app["s2_ctx"] = ctx

        app.router.add_view(S2_WS_PATH, S2WebSocketView)
        for path, view_cls in get_pairing_views():
            app.router.add_view(path, view_cls)
            if not path.endswith("/"):
                app.router.add_view(path + "/", view_cls)
        for path, view_cls in get_connection_views():
            app.router.add_view(path, view_cls)
            if not path.endswith("/"):
                app.router.add_view(path + "/", view_cls)

    # -- read-only accessors ----------------------------------------------

    @property
    def pairing_server(self) -> PairingServer | None:
        return self._pairing_server

    @property
    def context(self) -> S2Context | None:
        return self._ctx
