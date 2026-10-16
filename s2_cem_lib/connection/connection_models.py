# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from ..pairing.pairing_models import CommunicationProtocol, EndpointDescription, S2NodeDescription


class InitiateConnectionRequest(BaseModel):
    clientNodeId: UUID
    serverNodeId: UUID
    supportedS2MessageVersions: list[str]
    supportedCommunicationProtocols: list[CommunicationProtocol] = []
    clientNodeDescription: S2NodeDescription | None = None
    clientEndpointDescription: EndpointDescription | None = None


class InitiateSessionResponse(BaseModel):
    """Response to POST /connection/initiateSession"""
    selectedCommunicationProtocol: CommunicationProtocol
    selectedS2MessageVersion: str
    accessToken: str  # new pending access token
    serverNodeDescription: S2NodeDescription | None = None
    serverEndpointDescription: EndpointDescription | None = None


class WebSocketConnectionDetails(BaseModel):
    """WebSocket connection details returned after confirmAccessToken."""
    communicationProtocol: str = "WebSocket"
    websocketToken: str
    websocketUrl: str


class ConnectionDetailsError(str, Enum):
    """errorMessage values, must match the enum in the session-init spec."""

    NO_LONGER_PAIRED = "NoLongerPaired"
    INCOMPATIBLE_COMMUNICATION_PROTOCOLS = "IncompatibleCommunicationProtocols"
    INCOMPATIBLE_S2_MESSAGE_VERSIONS = "IncompatibleS2MessageVersions"
    PARSING_ERROR = "ParsingError"
    OTHER = "Other"


class ConnectionDetailsErrorMessage(BaseModel):
    errorMessage: ConnectionDetailsError
    additionalInfo: str | None = None


class UnpairRequest(BaseModel):
    clientNodeId: UUID
    serverNodeId: UUID
