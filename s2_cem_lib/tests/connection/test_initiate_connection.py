# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for InitiateSessionView and ConfirmAccessTokenView (S2 Connect session initiation)."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from s2_cem_lib.connection.views import ConfirmAccessTokenView, ConnectionApiVersionView, InitiateSessionView
from s2_cem_lib.pairing.pairing_server import PairingServer

from .conftest import TEST_S2_VERSION, add_paired_node, make_app, rejected_status

_SUPPORTED_VERSIONS = [TEST_S2_VERSION]
_SERVER_NODE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_INITIATE_BODY = {
    "supportedS2MessageVersions": _SUPPORTED_VERSIONS,
    "supportedCommunicationProtocols": ["WebSocket"],
    "serverNodeId": _SERVER_NODE_ID,
}


def make_request(server: PairingServer, node_id: uuid.UUID, bearer_token: str, body: dict):
    request = MagicMock()
    request.json = AsyncMock(return_value={**body, "clientNodeId": str(node_id)})
    request.headers = {"Authorization": f"Bearer {bearer_token}"}
    request.url.origin.return_value = "https://test.com"
    request.app = make_app(server)
    return request


# --- initiateSession ---

@pytest.mark.asyncio
async def test_initiate_session_missing_bearer_returns_401(server: PairingServer):
    request = MagicMock()
    request.headers = {}
    request.app = make_app(server)
    assert await rejected_status(InitiateSessionView(request).post()) == 401


@pytest.mark.asyncio
async def test_initiate_session_unknown_node_returns_401(server: PairingServer):
    unknown_id = uuid.uuid4()
    request = make_request(server, unknown_id, "sometoken", _INITIATE_BODY)
    assert await rejected_status(InitiateSessionView(request).post()) == 401


@pytest.mark.asyncio
async def test_initiate_session_returns_pending_access_token(server: PairingServer):
    node_id, token_str = add_paired_node(server)
    request = make_request(server, node_id, token_str, _INITIATE_BODY)
    response = await InitiateSessionView(request).post()
    assert response.status == 200
    payload = json.loads(response.text)
    assert "accessToken" in payload
    assert len(payload["accessToken"]) == 32
    assert "selectedS2MessageVersion" in payload
    assert payload["selectedS2MessageVersion"] == TEST_S2_VERSION
    assert "selectedCommunicationProtocol" in payload
    assert payload["selectedCommunicationProtocol"] == "WebSocket"


@pytest.mark.asyncio
async def test_initiate_session_old_token_not_yet_invalidated(server: PairingServer):
    """Old access token should still work until confirmAccessToken is called."""
    node_id, old_token_str = add_paired_node(server)

    r1 = await InitiateSessionView(
        make_request(server, node_id, old_token_str, _INITIATE_BODY)
    ).post()
    assert r1.status == 200
    new_token_str = json.loads(r1.text)["accessToken"]
    assert new_token_str != old_token_str

    # Old token should still work (not yet activated)
    r2 = await InitiateSessionView(
        make_request(server, node_id, old_token_str, _INITIATE_BODY)
    ).post()
    assert r2.status == 200


@pytest.mark.asyncio
async def test_initiate_session_invalid_body_returns_400(server: PairingServer):
    _node_id, token_str = add_paired_node(server)
    request = MagicMock()
    request.json = AsyncMock(return_value={"invalid_field": "garbage"})
    request.headers = {"Authorization": f"Bearer {token_str}"}
    request.url.origin.return_value = "https://test.com"
    request.app = make_app(server)
    response = await InitiateSessionView(request).post()
    assert response.status == 400
    assert "ParsingError" in response.text


# --- confirmAccessToken ---

@pytest.mark.asyncio
async def test_confirm_access_token_missing_bearer_returns_401(server: PairingServer):
    request = MagicMock()
    request.headers = {}
    request.app = make_app(server)
    assert await rejected_status(ConfirmAccessTokenView(request).post()) == 401


@pytest.mark.asyncio
async def test_confirm_access_token_activates_and_returns_ws_details(server: PairingServer):
    node_id, old_token_str = add_paired_node(server)

    # Step 1: initiateSession and then get pending token
    r1 = await InitiateSessionView(
        make_request(server, node_id, old_token_str, _INITIATE_BODY)
    ).post()
    assert r1.status == 200
    pending_token = json.loads(r1.text)["accessToken"]

    # Step 2: confirmAccessToken and then activate and get WS details
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {pending_token}"}
    request.url.origin.return_value = "https://test.com"
    request.app = make_app(server)
    response = await ConfirmAccessTokenView(request).post()
    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["communicationProtocol"] == "WebSocket"
    assert "websocketToken" in payload
    assert "websocketUrl" in payload

    # Old token now invalid
    assert await rejected_status(InitiateSessionView(make_request(server, node_id, old_token_str, _INITIATE_BODY)).post()) == 401


@pytest.mark.asyncio
async def test_confirm_access_token_unknown_returns_401(server: PairingServer):
    request = make_request(server, uuid.uuid4(), "nonexistent", {})
    assert await rejected_status(ConfirmAccessTokenView(request).post()) == 401


@pytest.mark.asyncio
async def test_connection_api_version_returns_v1(server: PairingServer):
    request = MagicMock()
    request.app = make_app(server)
    response = await ConnectionApiVersionView(request).get()
    assert response.status == 200
    import json
    assert json.loads(response.text) == ["v1"]

