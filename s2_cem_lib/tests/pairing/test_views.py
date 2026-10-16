# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for pairing HTTP views."""
import json
import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from s2_cem_lib.const import MAX_PENDING_PAIRING_OFFERS, REQUEST_PAIRING_RATE_LIMIT
from s2_cem_lib.pairing.pairing_models import Deployment, PairingErrorMessage
from s2_cem_lib.pairing.pairing_server import PairingServer
from s2_cem_lib.pairing.pairing_token import PairingToken
from s2_cem_lib.pairing.views import (
    CancelPreparePairingView,
    EndpointView,
    FinalizePairingView,
    NodesView,
    PairingApiVersionView,
    PostConnectionDetailsView,
    PreparePairingView,
    RequestConnectionDetailsView,
    RequestPairingView,
    WaitForPairingView,
)

from .conftest import CEM_DESCRIPTION, RM_BODY, add_paired_node, make_app, rejected_status


def _pairing_body(node_id: uuid.UUID, **extra):
    """Build a spec-compliant preparePairing/requestPairing body."""
    desc = {**RM_BODY["clientNodeDescription"], "id": str(node_id)}
    body = {
        "clientNodeDescription": desc,
        "clientEndpointDescription": RM_BODY["clientEndpointDescription"],
        "serverNodeId": str(CEM_DESCRIPTION.id),
    }
    body.update(extra)
    return body


def make_request(server, body, bearer=None, remote="1.2.3.4"):
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.remote = remote
    request.host = "test.local"
    request.app = make_app(server)
    if bearer:
        request.headers = {"Authorization": f"Bearer {bearer}"}
    else:
        request.headers = {}
    return request


# --- preparePairing ---

@pytest.mark.asyncio
async def test_prepare_pairing_returns_200(server: PairingServer):
    node_id = uuid.uuid4()
    body = _pairing_body(node_id)
    request = make_request(server, body)
    response = await PreparePairingView(request).post()
    assert response.status == 204
    assert node_id in server.pairing


@pytest.mark.asyncio
async def test_prepare_pairing_already_paired_returns_200(server: PairingServer):
    node_id, _ = add_paired_node(server)
    body = _pairing_body(node_id)
    request = make_request(server, body)
    response = await PreparePairingView(request).post()
    assert response.status == 204
    assert node_id in server.pairing


@pytest.mark.asyncio
async def test_cancel_prepare_pairing_removes_token(server: PairingServer):
    node_id = uuid.uuid4()
    body = _pairing_body(node_id)
    request = make_request(server, body)
    await PreparePairingView(request).post()
    assert node_id in server.pairing

    request2 = MagicMock()
    request2.json = AsyncMock(return_value={
        "clientNodeId": str(node_id),
        "serverNodeId": str(uuid.uuid4()),
    })
    request2.app = make_app(server)
    response = await CancelPreparePairingView(request2).post()

    assert response.status == 204
    assert node_id not in server.pairing


@pytest.mark.asyncio
async def test_prepare_pairing_is_rate_limited(server: PairingServer):
    node_id = uuid.uuid4()
    for _ in range(REQUEST_PAIRING_RATE_LIMIT):
        assert (await PreparePairingView(make_request(server, _pairing_body(node_id))).post()).status == 204

    assert (await PreparePairingView(make_request(server, _pairing_body(node_id))).post()).status == 429


@pytest.mark.asyncio
async def test_prepare_pairing_pending_offers_are_capped(server: PairingServer):
    for _ in range(MAX_PENDING_PAIRING_OFFERS):
        server.pairing[uuid.uuid4()] = MagicMock()

    # a new node is rejected while the cap is reached
    assert (await PreparePairingView(make_request(server, _pairing_body(uuid.uuid4()))).post()).status == 429
    # an existing offer is refreshed rather than counted again
    existing = next(iter(server.pairing))
    assert (await PreparePairingView(make_request(server, _pairing_body(existing))).post()).status == 204
    assert len(server.pairing) == MAX_PENDING_PAIRING_OFFERS


@pytest.mark.asyncio
async def test_prepare_pairing_rejects_markup_in_device_text(server: PairingServer):
    """Device-supplied text cannot carry markdown into the operator notification."""
    body = _pairing_body(uuid.uuid4())
    body["clientNodeDescription"]["brand"] = "**[click me](http://evil)**"
    response = await PreparePairingView(make_request(server, body)).post()
    assert response.status == 400
    assert not server.pairing


@pytest.mark.asyncio
async def test_prepare_pairing_rejects_overlong_device_text(server: PairingServer):
    body = _pairing_body(uuid.uuid4())
    body["clientNodeDescription"]["modelName"] = "x" * 65
    response = await PreparePairingView(make_request(server, body)).post()
    assert response.status == 400


@pytest.mark.asyncio
async def test_prepare_pairing_escapes_device_text_in_notification(server: PairingServer):
    body = _pairing_body(uuid.uuid4())
    body["clientNodeDescription"]["brand"] = "Acme-1.0 (EU)"
    request = make_request(server, body)
    assert (await PreparePairingView(request).post()).status == 204

    message = request.app["s2_ctx"].host_env.notify.call_args.kwargs["message"]
    assert "Acme\\-1\\.0 \\(EU\\)" in message


# --- requestPairing ---

_REQ_EXTRA = {
    "supportedS2MessageVersions": ["0.0.1-beta"],
    "supportedCommunicationProtocols": ["WebSocket"],
    "supportedHmacHashingAlgorithms": ["SHA256"],
    "clientHmacChallenge": "AAAA",
}


@pytest.mark.asyncio
async def test_request_pairing_valid_token_returns_200_and_pairs(server: PairingServer):
    node_id = uuid.uuid4()
    request = make_request(server, _pairing_body(node_id))
    await PreparePairingView(request).post()

    response = await RequestPairingView(
        make_request(server, _pairing_body(node_id, **_REQ_EXTRA))
    ).post()

    assert response.status == 200
    assert node_id in server.paired_nodes
    payload = json.loads(response.text)
    assert "pairingAttemptId" in payload
    assert len(payload["pairingAttemptId"]) == 32


@pytest.mark.asyncio
async def test_request_pairing_repairs_device_and_resets_tokens(server: PairingServer):
    node_id, old_access_token = add_paired_node(server)

    from datetime import datetime

    from s2_cem_lib.pairing.pairing_server import _PendingToken
    from s2_cem_lib.pairing.token import Token as _Token
    pending = _PendingToken(
        node=server.paired_nodes[node_id],
        new_access_token=_Token.access_token(),
        new_comm_token=_Token.comm_token(),
        timestamp=datetime.now(UTC),
    )
    server.pending_tokens.append(pending)
    mock_ws = AsyncMock()
    server.active_connections[node_id] = mock_ws

    await PreparePairingView(make_request(server, _pairing_body(node_id))).post()

    response = await RequestPairingView(
        make_request(server, _pairing_body(node_id, **_REQ_EXTRA))
    ).post()

    assert response.status == 200
    assert server.paired_nodes[node_id].access_token.value != old_access_token
    assert all(p.node.node_id != node_id for p in server.pending_tokens)
    mock_ws.close.assert_awaited_once()
    assert node_id not in server.active_connections


@pytest.mark.asyncio
async def test_request_pairing_no_token_returns_400(server: PairingServer):
    """Per spec: if no pairing token is available, return NoValidPairingTokenOnPairingServer."""
    node_id = uuid.uuid4()
    # Do NOT call preparePairing - no token exists
    response = await RequestPairingView(
        make_request(server, _pairing_body(node_id, **_REQ_EXTRA))
    ).post()

    assert response.status == 400
    assert PairingErrorMessage.NO_VALID_PAIRING_TOKEN_ON_SERVER in response.text


@pytest.mark.asyncio
async def test_request_pairing_prefers_per_client_token(server: PairingServer):
    """initiate_pairing stores the RM's token; the global token must not override it."""
    node_id = uuid.uuid4()
    server.pairing_token = PairingToken.pairing_token("GLOBAL")
    server.initiate_pairing(node_id, pairing_token="ABC123")

    response = await RequestPairingView(
        make_request(server, _pairing_body(node_id, **_REQ_EXTRA))
    ).post()

    assert response.status == 200
    payload = json.loads(response.text)
    expected = server.compute_hmac_challenge_response(
        "AAAA", "ABC123", deployment=Deployment.LAN, server_domain="test.local"
    )
    assert payload["clientHmacChallengeResponse"] == expected
    assert node_id not in server.pairing


@pytest.mark.asyncio
async def test_request_pairing_invalid_body_returns_400(server: PairingServer):
    request = make_request(server, {"invalid_field": "garbage"})
    response = await RequestPairingView(request).post()
    assert response.status == 400
    assert PairingErrorMessage.PARSING_ERROR in response.text


# --- other pairing ---

@pytest.mark.asyncio
async def test_prepare_pairing_invalid_body_returns_400(server: PairingServer):
    request = make_request(server, {"invalid_field": "garbage"})
    response = await PreparePairingView(request).post()
    assert response.status == 400
    assert PairingErrorMessage.PARSING_ERROR in response.text


@pytest.mark.asyncio
async def test_pairing_api_version_returns_v1(server: PairingServer):
    request = make_request(server, {})
    response = await PairingApiVersionView(request).get()
    assert response.status == 200
    assert json.loads(response.text) == ["v1"]


# --- requestConnectionDetails ---

@pytest.mark.asyncio
async def test_request_connection_details_missing_bearer_returns_401(server: PairingServer):
    request = make_request(server, {"serverHmacChallengeResponse": "dummy"})
    assert await rejected_status(RequestConnectionDetailsView(request).post()) == 401


@pytest.mark.asyncio
async def test_request_connection_details_unknown_attempt_returns_401(server: PairingServer):
    request = make_request(server, {"serverHmacChallengeResponse": "dummy"}, bearer="nonexistent")
    assert await rejected_status(RequestConnectionDetailsView(request).post()) == 401


# --- finalizePairing ---

@pytest.mark.asyncio
async def test_finalize_pairing_missing_bearer_returns_401(server: PairingServer):
    request = make_request(server, {"success": True})
    assert await rejected_status(FinalizePairingView(request).post()) == 401


@pytest.mark.asyncio
async def test_finalize_pairing_unknown_attempt_returns_401(server: PairingServer):
    request = make_request(server, {"success": True}, bearer="nonexistent")
    assert await rejected_status(FinalizePairingView(request).post()) == 401


@pytest.mark.asyncio
async def test_finalize_pairing_removes_attempt(server: PairingServer):
    node_id = uuid.uuid4()
    attempt = server.begin_pairing_attempt(node_id)
    request = make_request(server, {"success": True}, bearer=attempt.attempt_id)
    response = await FinalizePairingView(request).post()
    assert response.status == 204
    assert attempt.attempt_id not in server.pairing_attempts


# --- waitForPairing ---

@pytest.mark.asyncio
async def test_wait_for_pairing_returns_204_on_timeout(server: PairingServer):
    body = [{"clientNodeId": str(uuid.uuid4())}]
    request = make_request(server, body)
    response = await WaitForPairingView(request).post()
    assert response.status == 204


@pytest.mark.asyncio
async def test_wait_for_pairing_triggered_action_returns_200(server: PairingServer):
    import asyncio
    node_id = uuid.uuid4()
    body = [{"clientNodeId": str(node_id)}]
    request = make_request(server, body)

    async def _trigger():
        await asyncio.sleep(0.1)
        server.longpoll_trigger(node_id, "preparePairing")

    trigger_task = asyncio.create_task(_trigger())
    response = await WaitForPairingView(request).post()
    await trigger_task
    assert response.status == 200
    actions = json.loads(response.text)
    assert len(actions) == 1
    assert actions[0]["action"] == "preparePairing"
    assert actions[0]["clientNodeId"] == str(node_id)


@pytest.mark.asyncio
async def test_initiate_pairing_stores_token(server: PairingServer):
    node_id = uuid.uuid4()
    # No RM polling yet, so the trigger reports False but still stores the token.
    assert server.initiate_pairing(node_id, pairing_token="ABC123") is False
    assert node_id in server.pairing
    assert server.pairing[node_id].value == "ABC123"


@pytest.mark.asyncio
async def test_initiate_pairing_without_token(server: PairingServer):
    node_id = uuid.uuid4()
    assert server.initiate_pairing(node_id) is False
    assert node_id not in server.pairing


@pytest.mark.asyncio
async def test_wait_for_pairing_initiate_pairing_returns_request_pairing(server: PairingServer):
    import asyncio
    node_id = uuid.uuid4()
    body = [{"clientNodeId": str(node_id)}]
    request = make_request(server, body)

    async def _trigger():
        await asyncio.sleep(0.1)
        server.initiate_pairing(node_id, pairing_token="ABC123")

    trigger_task = asyncio.create_task(_trigger())
    response = await WaitForPairingView(request).post()
    await trigger_task
    assert response.status == 200
    actions = json.loads(response.text)
    assert len(actions) == 1
    assert actions[0]["action"] == "requestPairing"
    assert actions[0]["clientNodeId"] == str(node_id)
    assert server.pairing[node_id].value == "ABC123"


@pytest.mark.asyncio
async def test_wait_for_pairing_records_node_description(server: PairingServer):
    import asyncio
    node_id = uuid.uuid4()
    body = [{
        "clientNodeId": str(node_id),
        "clientNodeDescription": {
            "id": str(node_id),
            "brand": "TestBrand",
            "type": "EV Charger",
            "modelName": "TestRM",
            "role": "RM",
        },
        "clientEndpointDescription": {"deployment": "LAN"},
    }]
    request = make_request(server, body)

    async def _trigger():
        await asyncio.sleep(0.1)
        server.longpoll_trigger(node_id, "preparePairing")

    trigger_task = asyncio.create_task(_trigger())
    response = await WaitForPairingView(request).post()
    await trigger_task
    assert response.status == 200
    assert node_id in server.longpoll_nodes
    assert server.longpoll_nodes[node_id].brand == "TestBrand"
    assert node_id in server.longpoll_endpoints


@pytest.mark.asyncio
async def test_wait_for_pairing_records_error_message(server: PairingServer):
    import asyncio
    node_id = uuid.uuid4()
    body = [{
        "clientNodeId": str(node_id),
        "errorMessage": "NoValidTokenOnPairingClient",
    }]
    request = make_request(server, body)

    async def _trigger():
        await asyncio.sleep(0.1)
        server.longpoll_trigger(node_id, "preparePairing")

    trigger_task = asyncio.create_task(_trigger())
    response = await WaitForPairingView(request).post()
    await trigger_task
    assert response.status == 200
    assert server.longpoll_errors[node_id] == "NoValidTokenOnPairingClient"


# --- postConnectionDetails ---

@pytest.mark.asyncio
async def test_post_connection_details_missing_bearer_returns_401(server: PairingServer):
    request = make_request(server, {
        "serverHmacChallengeResponse": "dummy",
        "initiateSessionUrl": "https://example.com",
        "accessToken": "token",
    })
    assert await rejected_status(PostConnectionDetailsView(request).post()) == 401


@pytest.mark.asyncio
async def test_post_connection_details_unknown_attempt_returns_401(server: PairingServer):
    request = make_request(server, {
        "serverHmacChallengeResponse": "dummy",
        "initiateSessionUrl": "https://example.com",
        "accessToken": "token",
    }, bearer="nonexistent")
    assert await rejected_status(PostConnectionDetailsView(request).post()) == 401


# --- GET /endpoint ---

@pytest.mark.asyncio
async def test_endpoint_returns_deployment(server: PairingServer):
    request = make_request(server, {})
    response = await EndpointView(request).get()
    assert response.status == 200
    assert json.loads(response.text)["deployment"] == "LAN"


# --- GET /nodes ---

@pytest.mark.asyncio
async def test_nodes_returns_list(server: PairingServer):
    request = make_request(server, {})
    response = await NodesView(request).get()
    assert response.status == 200
    data = json.loads(response.text)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["role"] == "CEM"
