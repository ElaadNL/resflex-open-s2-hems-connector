# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Role(str, Enum):
    CEM = "CEM"
    RM = "RM"

class Deployment(str, Enum):
    WAN = "WAN"
    LAN = "LAN"

class CommunicationProtocol(str, Enum):
    """Supported communication protocols."""
    WEBSOCKET = "WebSocket"

class HmacHashingAlgorithm(str, Enum):
    """Supported HMAC hashing algorithms for challenge-response."""
    SHA256 = "SHA256"

class EndpointDescription(BaseModel):
    """Describes the endpoint (application) that hosts one or more nodes.
    See: https://docs.s2standard.org/s2-connect/1.0.0/discovery-pairing-authentication/
    """
    name: str | None = None
    logoUrl: str | None = None
    deployment: Deployment

# Device-supplied text is shown in an operator notification, so keep it to the characters a node description needs: 
# letters, digits and simple punctuation.
NODE_TEXT_PATTERN = r"^[\w \-.,&+/'()]+$"

class S2NodeDescription(BaseModel):
    """S2 Connect common.yml NodeDescription schema.

    The text fields end up in an operator notification, so they are limited in length and
    character set. Lengths match the 64 byte fields the reference firmware uses.
    """
    id: UUID
    brand: str = Field(max_length=64, pattern=NODE_TEXT_PATTERN)
    logoUrl: str | None = Field(default=None, max_length=128)
    type: str = Field(max_length=64, pattern=NODE_TEXT_PATTERN)
    modelName: str = Field(max_length=64, pattern=NODE_TEXT_PATTERN)
    userDefinedName: str | None = Field(default=None, max_length=64, pattern=NODE_TEXT_PATTERN)
    role: Role

class PairingInfo(BaseModel):
    """Displayed in the CEM UI for the user to hand to the RM."""
    pairingUrl: str
    token: str
    validUntil: datetime

class PreparePairingRequest(BaseModel):
    """POST /preparePairing — spec: clientNodeDescription, clientEndpointDescription, serverNodeId."""
    clientNodeDescription: S2NodeDescription
    clientEndpointDescription: EndpointDescription
    serverNodeId: UUID

class PairingRequest(BaseModel):
    """POST /requestPairing. Token is verified via HMAC, never sent directly."""
    clientNodeDescription: S2NodeDescription
    clientEndpointDescription: EndpointDescription
    nodeId: UUID | None = None
    nodeIdAlias: str | None = None
    supportedCommunicationProtocols: list[CommunicationProtocol]
    supportedS2MessageVersions: list[str] = Field(default_factory=list)
    supportedHmacHashingAlgorithms: list[HmacHashingAlgorithm]
    clientHmacChallenge: str  # Base64-encoded 32+ byte challenge
    forcePairing: bool = False

class PairingResponse(BaseModel):
    """Response to /requestPairing"""
    pairingAttemptId: str
    serverNodeDescription: S2NodeDescription
    serverEndpointDescription: EndpointDescription | None = None
    selectedHmacHashingAlgorithm: HmacHashingAlgorithm | None = None
    clientHmacChallengeResponse: str | None = None  # Base64-encoded HMAC response
    serverHmacChallenge: str | None = None  # Base64-encoded challenge for the client

class PairingErrorMessage(str, Enum):
    """S2 Connect Error messages"""
    INVALID_COMBINATION_OF_ROLES = "InvalidCombinationOfRoles"
    INCOMPATIBLE_S2_MESSAGE_VERSIONS = "IncompatibleS2MessageVersions"
    INCOMPATIBLE_HMAC_HASHING_ALGORITHMS = "IncompatibleHmacHashingAlgorithms"
    INCOMPATIBLE_COMMUNICATION_PROTOCOLS = "IncompatibleCommunicationProtocols"
    NODE_NOT_FOUND = "NodeNotFound"
    NO_NODE_ID_PROVIDED = "NoNodeIdProvided"
    NO_VALID_PAIRING_TOKEN_ON_SERVER = "NoValidPairingTokenOnPairingServer"
    PARSING_ERROR = "ParsingError"
    OTHER = "Other"

class PairingResponseErrorMessage(BaseModel):
    errorMessage: PairingErrorMessage
    additionalInfo: str | None = None


class ConnectionDetailsRequest(BaseModel):
    """POST /requestConnectionDetails 

    client delivers serverHmacChallengeResponse.
    """
    serverHmacChallengeResponse: str  # Base64-encoded HMAC response


class ConnectionDetailsResponse(BaseModel):
    """Response to /requestConnectionDetails 
    
    connection info for the client.
    """
    initiateSessionUrl: str
    accessToken: str


class PostConnectionDetailsRequest(BaseModel):
    """POST /postConnectionDetails — client sends connection details + HMAC response."""
    serverHmacChallengeResponse: str  # Base64-encoded HMAC response
    initiateSessionUrl: str
    accessToken: str


class FinalizePairingRequest(BaseModel):
    """POST /finalizePairing 
     
    client confirms pairing success or failure.
    """
    success: bool


class WaitForPairingEntry(BaseModel):
    """A single entry in the POST /waitForPairing request array."""
    clientNodeId: UUID
    clientNodeDescription: S2NodeDescription | None = None
    clientEndpointDescription: EndpointDescription | None = None
    errorMessage: Literal["NoValidTokenOnPairingClient"] | None = None


class WaitForPairingAction(BaseModel):
    """A single action in the POST /waitForPairing response array."""
    clientNodeId: UUID
    action: Literal["sendNodeDescription", "preparePairing", "cancelPreparePairing", "requestPairing"]

