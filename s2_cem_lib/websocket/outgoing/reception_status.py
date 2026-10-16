# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""ReceptionStatus sender. Sent as an acknowledgement for every received S2 message that does not have a response already."""
from __future__ import annotations

import uuid

from aiohttp import web
from s2python.common import ReceptionStatus, ReceptionStatusValues


async def send_reception_status(
    ws: web.WebSocketResponse,
    subject_message_id: uuid.UUID,
    status: ReceptionStatusValues,
    diagnostic_label: str | None = None,
) -> None:
    """Send a ReceptionStatus acknowledging a received message."""
    ack = ReceptionStatus(
        subject_message_id=subject_message_id,
        status=status,
        diagnostic_label=diagnostic_label,
    )
    await ws.send_str(ack.to_json())
