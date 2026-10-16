# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures and helpers for pairing tests."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from s2_cem_lib.const import DOMAIN
from s2_cem_lib.pairing.pairing_models import (
    Deployment,
    EndpointDescription,
    PreparePairingRequest,
    Role,
    S2NodeDescription,
)
from s2_cem_lib.pairing.pairing_server import (
    PairingServer,
    _PairedNode,
)
from s2_cem_lib.pairing.token import Token
from s2_cem_lib.s2_cem.context import S2Context

TEST_S2_VERSION = "0.0.1-beta"

_ENDPOINT = EndpointDescription(deployment=Deployment.LAN)

CEM_DESCRIPTION = S2NodeDescription(
    id=uuid.uuid4(),
    brand="TestBrand",
    type="CEM",
    modelName="TestCEM",
    role=Role.CEM,
)

RM_DESCRIPTION = S2NodeDescription(
    id=uuid.uuid4(),
    brand="TestBrand",
    type="EV Charger",
    modelName="TestRM",
    role=Role.RM,
)

RM_BODY = {
    "clientNodeDescription": { "id": None,
        "brand": "TestBrand",
        "type": "EV Charger",
        "modelName": "TestRM",
        "role": "RM",
    },
    "clientEndpointDescription": {"deployment": "LAN"},
}


@pytest.fixture
def server():
    return PairingServer(role=Role.CEM, node_description=CEM_DESCRIPTION,
                         endpoint_description=_ENDPOINT,
                         supported_s2_message_versions=[TEST_S2_VERSION],
                         certificate_fingerprint=b"\x00" * 32)


def make_rm_client_info(versions=None):
    """Return a PairingRequest for _verify_can_pair tests (which checks versions).
    Returns PreparePairingRequest when versions is None (backward compat)."""
    if versions is not None:
        from s2_cem_lib.pairing.pairing_models import (
            CommunicationProtocol,
            HmacHashingAlgorithm,
            PairingRequest,
        )
        return PairingRequest(
            clientNodeDescription=RM_DESCRIPTION,
            clientEndpointDescription=_ENDPOINT,
            supportedCommunicationProtocols=[CommunicationProtocol.WEBSOCKET],
            supportedS2MessageVersions=versions,
            supportedHmacHashingAlgorithms=[HmacHashingAlgorithm.SHA256],
            clientHmacChallenge="AAAA",
        )
    return PreparePairingRequest(
        clientNodeDescription=RM_DESCRIPTION,
        clientEndpointDescription=_ENDPOINT,
        serverNodeId=CEM_DESCRIPTION.id,
    )


def make_hass(server: PairingServer) -> MagicMock:
    """Return a mock hass with the server registered in hass.data."""
    hass = MagicMock()
    hass.data = {DOMAIN: {"test": server}}
    hass.async_create_task = MagicMock(side_effect=lambda coro, **_: coro.close())
    return hass


def _make_mock_s2_ctx(server: PairingServer) -> S2Context:
    """Create a mock S2Context whose ports are all MagicMocks."""
    host_env = MagicMock()
    host_env.notify = MagicMock()
    host_env.dismiss = MagicMock()
    persistence = MagicMock()
    persistence.save = AsyncMock()
    persistence.load = AsyncMock()
    persistence.remove = AsyncMock()
    scheduler = MagicMock()
    scheduler.create_task = MagicMock(side_effect=lambda coro: coro.close())
    event_bus = MagicMock()
    return S2Context(
        server,
        host_env=host_env,
        scheduler=scheduler,
        persistence=persistence,
        event_bus=event_bus,
    )


def make_app(server: PairingServer) -> dict:
    """Return a mock request.app dict with hass and s2_ctx."""
    return {
        "hass": make_hass(server),
        "s2_ctx": _make_mock_s2_ctx(server),
    }


def make_mock_request(body: dict):
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app = {"hass": MagicMock()}
    return request


def add_paired_node(server: PairingServer):
    """Add a paired RM node and return (node_id, access_token_str)."""
    access_token = Token.access_token()
    node_id = uuid.uuid4()
    server.paired_nodes[node_id] = _PairedNode(access_token, RM_DESCRIPTION, node_id)
    return node_id, access_token.value


async def rejected_status(coro) -> int:
    """Await a view call that must reject the caller and return the status it raises."""
    with pytest.raises(web.HTTPUnauthorized) as exc:
        await coro
    return exc.value.status
