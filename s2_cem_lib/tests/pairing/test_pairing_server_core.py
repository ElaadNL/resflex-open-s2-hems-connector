# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for PairingServer core logic (_verify_can_pair, generate_pairing_info)."""
import datetime
import uuid

from s2_cem_lib.const import MAX_RATE_LIMIT_KEYS, RATE_LIMIT_WINDOW_SECONDS, REQUEST_PAIRING_RATE_LIMIT
from s2_cem_lib.pairing.pairing_models import (
    PairingErrorMessage,
    PreparePairingRequest,
)
from s2_cem_lib.pairing.pairing_server import PairingServer

from .conftest import _ENDPOINT, CEM_DESCRIPTION, RM_DESCRIPTION, make_rm_client_info

# --- _verify_can_pair ---

def test_verify_can_pair_valid_returns_none(server: PairingServer):
    assert server._verify_can_pair(make_rm_client_info()) is None


def test_verify_can_pair_unknown_server_node_returns_error(server: PairingServer):
    """Spec: does it recognize the serverNodeId? -> 400 NodeNotFound."""
    body = PreparePairingRequest(
        clientNodeDescription=RM_DESCRIPTION,
        clientEndpointDescription=_ENDPOINT,
        serverNodeId=uuid.uuid4(),
    )
    response = server._verify_can_pair(body)
    assert response is not None
    assert PairingErrorMessage.NODE_NOT_FOUND in response.text


def test_verify_can_pair_same_role_returns_error(server: PairingServer):
    cem_client = PreparePairingRequest(
        clientNodeId=uuid.uuid4(),
        clientNodeDescription=CEM_DESCRIPTION,
        clientEndpointDescription=_ENDPOINT,
        serverNodeId=CEM_DESCRIPTION.id,
    )
    response = server._verify_can_pair(cem_client)
    assert response is not None
    assert PairingErrorMessage.INVALID_COMBINATION_OF_ROLES in response.text


def test_verify_can_pair_no_common_versions_returns_error(server: PairingServer):
    response = server._verify_can_pair(make_rm_client_info(versions=["99.99.99"]))
    assert response is not None
    assert PairingErrorMessage.INCOMPATIBLE_S2_MESSAGE_VERSIONS in response.text


# --- allow_pairing_request ---

def test_idle_rate_limit_records_are_dropped(server: PairingServer):
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS + 1)
    server._request_pairing_attempts["old-node"] = [stale]

    assert server.allow_pairing_request("new-node") is True
    assert "old-node" not in server._request_pairing_attempts


def test_rate_limit_does_not_grow_the_record(server: PairingServer):
    """A node that keeps hammering must not extend its own record without bound."""
    for _ in range(REQUEST_PAIRING_RATE_LIMIT + 5):
        server.allow_pairing_request("node")

    assert len(server._request_pairing_attempts["node"]) == REQUEST_PAIRING_RATE_LIMIT


def test_rate_limit_key_count_is_capped(server: PairingServer):
    now = datetime.datetime.now(datetime.UTC)
    for i in range(MAX_RATE_LIMIT_KEYS):
        server._request_pairing_attempts[f"node-{i}"] = [now]

    assert server.allow_pairing_request("one-too-many") is False
    assert len(server._request_pairing_attempts) == MAX_RATE_LIMIT_KEYS
    # keys already tracked are still served
    assert server.allow_pairing_request("node-0") is True


# --- generate_pairing_info ---

def test_generate_pairing_info_sets_pairing_token(server: PairingServer):
    assert server.pairing_token is None
    server.generate_pairing_info("https://test.com/api/s2_pebc/pairing")
    assert server.pairing_token is not None
    assert len(server.pairing_token.value) == 6
    assert server.pairing_token.valid_till is not None
    # check that its valid for around 5 minutes (just less than to account for test execution time)
    assert server.pairing_token.valid_till.timestamp() > datetime.datetime.now(datetime.UTC).timestamp() + 290

def test_generate_pairing_info_returns_correct_url(server: PairingServer):
    url = "https://test.com/api/s2_pebc/pairing"
    assert server.generate_pairing_info(url).pairingUrl == url


def test_generate_pairing_info_token_matches_server_token(server: PairingServer):
    info = server.generate_pairing_info("https://test.com/api/s2_pebc/pairing")
    assert info.token == server.pairing_token.value


def test_generate_pairing_info_replaces_old_token(server: PairingServer):
    info1 = server.generate_pairing_info("https://test.com/api/s2_pebc/pairing")
    info2 = server.generate_pairing_info("https://test.com/api/s2_pebc/pairing")
    assert info1.token != info2.token
    assert server.pairing_token.value == info2.token


# --- pairing attempt IDs ---

def test_begin_pairing_attempt_creates_valid_id(server: PairingServer):
    node_id = uuid.uuid4()
    attempt = server.begin_pairing_attempt(node_id)
    assert len(attempt.attempt_id) == 32
    assert attempt.client_node_id == node_id
    assert attempt.attempt_id in server.pairing_attempts


def test_get_pairing_attempt_returns_valid(server: PairingServer):
    node_id = uuid.uuid4()
    attempt = server.begin_pairing_attempt(node_id)
    found = server.get_pairing_attempt(attempt.attempt_id)
    assert found is not None
    assert found.attempt_id == attempt.attempt_id


def test_get_pairing_attempt_unknown_returns_none(server: PairingServer):
    assert server.get_pairing_attempt("nonexistent") is None


def test_pairing_attempts_are_unique(server: PairingServer):
    a1 = server.begin_pairing_attempt(uuid.uuid4())
    a2 = server.begin_pairing_attempt(uuid.uuid4())
    assert a1.attempt_id != a2.attempt_id


def test_begin_pairing_attempt_prunes_expired(server: PairingServer):
    from datetime import timedelta
    node_id = uuid.uuid4()
    attempt = server.begin_pairing_attempt(node_id)
    # artificially expire the attempt
    attempt.created_at = attempt.created_at - timedelta(seconds=999)
    assert attempt.is_expired()

    # new attempt should prune the expired one
    server.begin_pairing_attempt(uuid.uuid4())
    assert server.get_pairing_attempt(attempt.attempt_id) is None


# --- certificate fingerprint ---

def test_certificate_fingerprint_hex_returns_colon_separated(server: PairingServer):
    server.certificate_fingerprint = bytes(range(32))
    hex_str = server.certificate_fingerprint_hex
    assert hex_str == ":".join(f"{b:02X}" for b in range(32))


def test_certificate_fingerprint_hex_none_when_not_set(server: PairingServer):
    server.certificate_fingerprint = None  # override fixture
    assert server.certificate_fingerprint_hex is None


# --- HMAC challenge-response ---

def test_generate_hmac_challenge_returns_base64(server: PairingServer):
    challenge = server.generate_hmac_challenge()
    import base64
    decoded = base64.b64decode(challenge)
    assert len(decoded) == 32


def test_generate_hmac_challenge_is_unique(server: PairingServer):
    c1 = server.generate_hmac_challenge()
    c2 = server.generate_hmac_challenge()
    assert c1 != c2


def test_compute_hmac_challenge_response_lan(server: PairingServer):
    server.certificate_fingerprint = b"\x00" * 32
    challenge = server.generate_hmac_challenge()
    response = server.compute_hmac_challenge_response(challenge, "ABCD", deployment="LAN")
    import base64
    assert len(base64.b64decode(response)) == 32


def test_compute_hmac_challenge_response_wan(server: PairingServer):
    challenge = server.generate_hmac_challenge()
    response = server.compute_hmac_challenge_response(
        challenge, "ABCD", deployment="WAN", server_domain="test.local"
    )
    import base64
    assert len(base64.b64decode(response)) == 32


def test_compute_hmac_response_deterministic(server: PairingServer):
    server.certificate_fingerprint = b"\x00" * 32
    challenge = "AAAA"  # same challenge
    r1 = server.compute_hmac_challenge_response(challenge, "TOKEN", deployment="LAN")
    r2 = server.compute_hmac_challenge_response(challenge, "TOKEN", deployment="LAN")
    assert r1 == r2


def test_compute_hmac_response_differs_with_different_token(server: PairingServer):
    server.certificate_fingerprint = b"\x00" * 32
    challenge = "AAAA"
    r1 = server.compute_hmac_challenge_response(challenge, "TOKEN1", deployment="LAN")
    r2 = server.compute_hmac_challenge_response(challenge, "TOKEN2", deployment="LAN")
    assert r1 != r2


def test_compute_hmac_lan_requires_fingerprint(server: PairingServer):
    import pytest
    server.certificate_fingerprint = None  # override fixture for this test
    with pytest.raises(ValueError, match="Certificate fingerprint"):
        server.compute_hmac_challenge_response("AAAA", "TOKEN", deployment="LAN")
