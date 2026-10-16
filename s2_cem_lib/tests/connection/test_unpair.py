# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for UnpairView (POST /connection/v1/unpair)."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from s2_cem_lib.connection.views import UnpairView
from s2_cem_lib.pairing.pairing_server import PairingServer

from .conftest import add_paired_node, make_app, rejected_status

_SERVER_NODE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def make_unpair_request(server: PairingServer, bearer_token: str, node_id: uuid.UUID):
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {bearer_token}"}
    request.json = AsyncMock(return_value={
        "clientNodeId": str(node_id),
        "serverNodeId": str(uuid.UUID(_SERVER_NODE_ID)),
    })
    request.app = make_app(server)
    return request


@pytest.mark.asyncio
async def test_unpair_removes_node(server: PairingServer):
    node_id, token_str = add_paired_node(server)
    response = await UnpairView(make_unpair_request(server, token_str, node_id)).post()
    assert response.status == 204
    assert node_id not in server.paired_nodes


@pytest.mark.asyncio
async def test_unpair_missing_token_returns_401(server: PairingServer):
    request = MagicMock()
    request.headers = {}
    request.app = make_app(server)
    assert await rejected_status(UnpairView(request).post()) == 401


@pytest.mark.asyncio
async def test_unpair_unknown_node_returns_404(server: PairingServer):
    _, token_str = add_paired_node(server)
    response = await UnpairView(make_unpair_request(server, token_str, uuid.uuid4())).post()
    assert response.status == 404


@pytest.mark.asyncio
async def test_unpair_wrong_token_returns_401(server: PairingServer):
    node_id, _ = add_paired_node(server)
    assert await rejected_status(UnpairView(make_unpair_request(server, "wrongtoken", node_id)).post()) == 401


@pytest.mark.asyncio
async def test_unpair_closes_active_websocket(server: PairingServer):
    node_id, token_str = add_paired_node(server)
    mock_ws = AsyncMock()
    server.active_connections[node_id] = mock_ws
    await UnpairView(make_unpair_request(server, token_str, node_id)).post()
    mock_ws.close.assert_called_once()
    assert node_id not in server.active_connections


@pytest.mark.asyncio
async def test_unpair_invalid_body_returns_400(server: PairingServer):
    _, token_str = add_paired_node(server)
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token_str}"}
    request.json = AsyncMock(return_value={"invalid_field": "garbage"})
    request.app = make_app(server)
    response = await UnpairView(request).post()
    assert response.status == 400
