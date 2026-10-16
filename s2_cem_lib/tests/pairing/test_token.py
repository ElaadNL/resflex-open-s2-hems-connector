# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for Token generation and validation."""
from datetime import UTC, datetime, timedelta

from s2_cem_lib.pairing.pairing_token import PairingToken
from s2_cem_lib.pairing.token import Token


def test_pairing_token_length():
    token = PairingToken.pairing_token()
    assert len(token.value) == 6


def test_access_token_length():
    token = Token.access_token()
    assert len(token.value) == 32


def test_comm_token_length():
    token = Token.comm_token()
    assert len(token.value) == 32


def test_token_is_alphanumeric():
    token = Token.access_token()
    assert token.value.isalnum()


def test_check_correct_token():
    token = Token.access_token()
    assert token.check(token.value) is True


def test_check_wrong_token():
    token = Token.access_token()
    assert token.check("wrongtoken") is False


def test_check_non_ascii_token():
    token = Token.access_token()
    assert token.check("caf\u00e9") is False


def test_check_expired_token():
    token = Token.access_token()
    # Force expiry by backdating valid_till
    token.valid_till = datetime.now(UTC) - timedelta(seconds=1)
    assert token.check(token.value) is False


def test_expired_returns_false_for_fresh_token():
    token = Token.access_token()
    assert token.expired() is False


def test_expired_returns_true_for_past_token():
    token = Token.access_token()
    token.valid_till = datetime.now(UTC) - timedelta(seconds=1)
    assert token.expired() is True


def test_two_tokens_are_unique():
    a = Token.access_token()
    b = Token.access_token()
    assert a.value != b.value
