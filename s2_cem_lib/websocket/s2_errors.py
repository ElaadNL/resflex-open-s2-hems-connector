# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Custom S2 exception types for signalling specific ReceptionStatus values."""
from __future__ import annotations


class InvalidContentError(Exception):
    """Message contents are invalid (e.g. reference a non-existing ID).
    Consequence: message is ignored, proceed if possible.
    """


class TemporaryError(Exception):
    """Receiver encountered a transient error.
    Consequence: RM should resend the message.
    """
