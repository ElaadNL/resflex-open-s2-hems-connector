# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import timedelta

from ..const import PAIRING_TOKEN_TIMEOUT_MINUTES, STATIC_PAIRING_TOKEN_TIMEOUT_DAYS
from .token import Token


class PairingToken(Token):

    @property
    def value(self) -> str:
        return self._value

    @classmethod
    def pairing_token(cls, value: str | None = None):
        """Return a new PairingToken.

        If value is given the token has that fixed value and expires after STATIC_PAIRING_TOKEN_TIMEOUT_DAYS.
        Otherwise a random value is generated and it expires after PAIRING_TOKEN_TIMEOUT_MINUTES.
        """
        if value is not None:
            tok = PairingToken(6, timedelta(days=STATIC_PAIRING_TOKEN_TIMEOUT_DAYS))
            tok._value = value
            return tok
        return PairingToken(6, timedelta(minutes=PAIRING_TOKEN_TIMEOUT_MINUTES))
