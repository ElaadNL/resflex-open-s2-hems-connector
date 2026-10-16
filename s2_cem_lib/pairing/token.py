# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import secrets
import string
from datetime import UTC, datetime, timedelta

from ..const import ACCESS_TOKEN_TIMEOUT_DAYS, COMM_TOKEN_TIMEOUT_SECONDS


def generate_pairing_attempt_id() -> str:
    """Generate a cryptographically random pairingAttemptId

    24 random bytes, Base64 encoded = 32 character string.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).decode("ascii")

class Token:
    def __init__(self, length: int, valid_duration: timedelta):
        self._value = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))
        self.valid_till = datetime.now(UTC) + valid_duration

    @property
    def value(self) -> str:
        return self._value

    @classmethod
    def restore(cls, value: str, valid_till: datetime) -> Token:
        """Reconstruct a Token from persisted data (bypasses normal generation)."""
        token = cls.__new__(cls)
        token._value = value
        token.valid_till = valid_till
        return token

    @classmethod
    def comm_token(cls):
        """Generate a communication token valid for a short duration."""
        return Token(32, timedelta(seconds=COMM_TOKEN_TIMEOUT_SECONDS))

    @classmethod
    def access_token(cls):
        """
        Generate an access token
        :return: The token
        """
        return Token(32, timedelta(days=ACCESS_TOKEN_TIMEOUT_DAYS))

    def check(self, other_token: str) -> bool:
        """
        Checks if the token matches other_token and is still valid

        :param other_token: The token to compare this token against
        :return: If the token matches and is still valid
        """
        # constant-time compare
        # encode so a non-ASCII header value cannot raise TypeError
        token_match = secrets.compare_digest(self._value.encode(), other_token.encode())
        is_expired = self.expired()
        return token_match and not is_expired

    def expired(self):
        """
        Checks if the token has expired

        :return: If the token has expired
        """
        return datetime.now(UTC) > self.valid_till
