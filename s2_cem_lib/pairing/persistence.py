# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Persistence helpers for PairingServer state (paired nodes)."""
from __future__ import annotations

import logging
import uuid as _uuid_mod
from datetime import datetime
from typing import TYPE_CHECKING

from .pairing_models import S2NodeDescription
from .pairing_server import PairingServer, _PairedNode
from .token import Token

if TYPE_CHECKING:
    from ..s2_cem.ports import Persistence

_LOGGER = logging.getLogger(__name__)


async def restore_paired_nodes(
    persistence: Persistence, pairing_server: PairingServer
) -> None:
    """Load persisted paired nodes into pairing_server on startup."""
    stored: dict = await persistence.load() or {}
    for node_id_str, data in stored.items():
        try:
            node_id = _uuid_mod.UUID(node_id_str)
            access_token = Token.restore(
                data["access_token_value"],
                datetime.fromisoformat(data["access_token_valid_till"]),
            )
            node_desc = S2NodeDescription(**data["node_description"])
            cert_fp_hex = data.get("certificate_fingerprint_hex")
            cert_fp = bytes.fromhex(cert_fp_hex) if cert_fp_hex else None
            pairing_server.paired_nodes[node_id] = _PairedNode(access_token, node_desc, node_id, certificate_fingerprint=cert_fp)
            _LOGGER.info("S2 PEBC: restored paired node %s (%s %s)", node_id, node_desc.brand, node_desc.modelName)
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("S2 PEBC: skipping corrupt node entry %s: %s", node_id_str, exc)


async def save_paired_nodes(
    persistence: Persistence, pairing_server: PairingServer
) -> None:
    """Persist all currently paired nodes through the Persistence port."""
    data = {
        str(node_id): {
            "access_token_value": node.access_token.value,
            "access_token_valid_till": node.access_token.valid_till.isoformat(),
            # mode json so the dict stays serialisable for file and database stores
            "node_description": node.node_description.model_dump(mode="json"),
            **({"certificate_fingerprint_hex": node.certificate_fingerprint.hex()} if node.certificate_fingerprint else {}),
        }
        for node_id, node in pairing_server.paired_nodes.items()
    }
    await persistence.save(data)
