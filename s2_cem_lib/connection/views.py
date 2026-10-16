# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""HTTP views for the S2 Connection API.

Connection API (/connection/initiateSession, /connection/confirmAccessToken, /connection/unpair)

Views read ``request.app["s2_ctx"]`` for the pairing server and ports.

Auth failures are raised so middleware sees the attempt.
"""
from __future__ import annotations

import re

from aiohttp import web

from ..const import S2_CONNECT_API_VERSION, S2_CONNECTION_PATH, S2_WS_PATH
from ..pairing.pairing_models import CommunicationProtocol
from ..pairing.pairing_server import _error
from ..pairing.persistence import save_paired_nodes
from ..pairing.token import Token
from ..s2_cem.context import S2Context
from .connection_models import (
    ConnectionDetailsError,
    InitiateConnectionRequest,
    InitiateSessionResponse,
    UnpairRequest,
    WebSocketConnectionDetails,
)

_503 = web.Response(status=503, reason="Engine not initialized")

CONTENT_TYPE_JSON = "application/json"

_BEARER_RE = re.compile(r"(?i)bearer\s+(\S+)")


def _extract_bearer(request: web.Request) -> str | None:
    """Extract Bearer token from Authorization header, or None."""
    auth = request.headers.get("Authorization", "")
    m = _BEARER_RE.match(auth)
    return m.group(1) if m else None


def _get_ctx(request: web.Request) -> S2Context | None:
    return request.app.get("s2_ctx")


def get_views() -> list[tuple[str, type[web.View]]]:
    """Return (path, ViewClass) pairs for the Connection API."""
    base = S2_CONNECTION_PATH
    ver = f"{base}/{S2_CONNECT_API_VERSION}"
    return [
        (f"{base}", ConnectionApiVersionView),
        (f"{ver}/initiateSession", InitiateSessionView),
        (f"{ver}/confirmAccessToken", ConfirmAccessTokenView),
        (f"{ver}/unpair", UnpairView),
    ]


class ConnectionApiVersionView(web.View):
    """GET /connection/ — return supported API major versions."""

    async def get(self) -> web.Response:
        return web.json_response([S2_CONNECT_API_VERSION])


class InitiateSessionView(web.View):
    """POST /connection/initiateSession

    Generates a pending access token and returns it. The client must call
    /confirmAccessToken within 15 seconds to activate it.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        bearer_token = _extract_bearer(self.request)
        if bearer_token is None:
            raise web.HTTPUnauthorized()

        try:
            body = InitiateConnectionRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, ConnectionDetailsError.PARSING_ERROR, additional_info=str(exc))

        node = server.paired_nodes.get(body.clientNodeId)
        # spec 401: one check for both, so an unknown node id looks the same as a bad token
        if node is None or not node.access_token.check(bearer_token):
            raise web.HTTPUnauthorized()

        # Check S2 message version compatibility
        server_supported = server.supported_s2_message_versions
        common = [v for v in server_supported if v in body.supportedS2MessageVersions]
        if not common:
            return _error(400, ConnectionDetailsError.INCOMPATIBLE_S2_MESSAGE_VERSIONS)

        # Check communication protocol compatibility
        if body.supportedCommunicationProtocols:
            server_protocols = {p.value for p in server.supported_communication_protocols}
            client_protocols = {p.value for p in body.supportedCommunicationProtocols}
            if not server_protocols.intersection(client_protocols):
                return _error(400, ConnectionDetailsError.INCOMPATIBLE_COMMUNICATION_PROTOCOLS)

        # Update node/endpoint description if provided
        if body.clientNodeDescription is not None:
            node.node_description = body.clientNodeDescription
        if body.clientEndpointDescription is not None:
            node.endpoint_description = body.clientEndpointDescription

        # Generate pending access token
        try:
            new_access_token, _pending = server.begin_pending_access_token(body.clientNodeId)
        except KeyError:
            raise web.HTTPUnauthorized() from None

        selected_protocol = CommunicationProtocol.WEBSOCKET
        response = InitiateSessionResponse(
            selectedCommunicationProtocol=selected_protocol,
            selectedS2MessageVersion=common[0],
            accessToken=new_access_token.value,
            serverNodeDescription=server.node_description,
            serverEndpointDescription=server.endpoint_description,
        )
        return web.Response(status=200, body=response.model_dump_json(), content_type=CONTENT_TYPE_JSON)


class ConfirmAccessTokenView(web.View):
    """POST /connection/confirmAccessToken

    Client confirms it persisted the pending access token. Server activates it
    and returns WebSocket connection details.
    """

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        pending_token_str = _extract_bearer(self.request)
        if pending_token_str is None:
            raise web.HTTPUnauthorized()

        comm_token = Token.comm_token()
        activated = server.activate_pending_access_token(pending_token_str, comm_token=comm_token)
        if activated is None:
            raise web.HTTPUnauthorized()

        await save_paired_nodes(ctx.persistence, server)

        ws_url = str(self.request.url.origin()).replace("http", "ws", 1) + S2_WS_PATH
        response = WebSocketConnectionDetails(
            communicationProtocol="WebSocket",
            websocketToken=comm_token.value,
            websocketUrl=ws_url,
        )
        return web.Response(status=200, body=response.model_dump_json(), content_type=CONTENT_TYPE_JSON)


class UnpairView(web.View):
    """POST /connection/unpair — RM-initiated unpair."""

    async def post(self) -> web.Response:
        ctx = _get_ctx(self.request)
        if ctx is None:
            return _503
        server = ctx.pairing_server

        bearer_token = _extract_bearer(self.request)
        if bearer_token is None:
            raise web.HTTPUnauthorized()

        try:
            body = UnpairRequest(**await self.request.json())
        except (ValueError, TypeError) as exc:
            return _error(400, str(exc))
        node = server.paired_nodes.get(body.clientNodeId)
        if node is None:
            return _error(404, "clientNodeId not found")

        if not node.access_token.check(bearer_token):
            raise web.HTTPUnauthorized()

        ws = server.unpair_node(node.node_id)
        if ws is not None:
            await ws.close()
        await save_paired_nodes(ctx.persistence, server)

        return web.Response(status=204)
