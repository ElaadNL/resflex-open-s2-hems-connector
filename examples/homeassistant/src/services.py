# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""HA service call handlers for the S2 PEBC integration."""
from __future__ import annotations

import logging
from uuid import UUID

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from s2_cem_lib.const import DOMAIN
from s2_cem_lib.pairing.persistence import save_paired_nodes
from s2_cem_lib.websocket.node_state import PowerEnvelopeSpec, get_state
from s2_cem_lib.websocket.outgoing.pebc_instruction import send_pebc_instruction
from s2_cem_lib.websocket.outgoing.revoke_object import send_revoke_instruction
from s2_cem_lib.websocket.outgoing.session_request import send_session_request
from s2python.common import CommodityQuantity, SessionRequestType

from .s2_cem.ha_adapters import HAPersistence

_LOGGER = logging.getLogger(__name__)


def _iter_pairing_servers(hass: HomeAssistant):
    """Yield only objects that expose active WebSocket connections."""
    for value in hass.data.get(DOMAIN, {}).values():
        if hasattr(value, "active_connections"):
            yield value


async def _notify_and_close(ws, node_id: UUID, node_id_str: str) -> None:
    try:
        await send_session_request(ws, SessionRequestType.RECONNECT, node_id=node_id_str, diagnostic_label="Unpaired by CEM")
    except (ConnectionError, RuntimeError) as exc:
        _LOGGER.warning("Could not send SessionRequest(RECONNECT) to node %s: %s", node_id, exc)
    await ws.close()


async def handle_unpair_node(hass: HomeAssistant, call: ServiceCall) -> None:
    """Unpair a Resource Manager node (CEM-initiated)."""
    node_id_str: str = call.data["node_id"]
    try:
        node_id = UUID(node_id_str)
    except ValueError as exc:
        raise ServiceValidationError(f"Invalid node_id {node_id_str!r}: {exc}") from exc

    for server in _iter_pairing_servers(hass):
        if node_id not in server.paired_nodes:
            continue
        try:
            ws = server.unpair_node(node_id)
        except KeyError:
            continue
        if ws is not None:
            await _notify_and_close(ws, node_id, node_id_str)
        await save_paired_nodes(HAPersistence(hass, f"{DOMAIN}_paired_nodes"), server)
        _LOGGER.info("CEM-initiated unpair: removed node %s", node_id)
        return

    raise ServiceValidationError(f"No paired node found with id {node_id_str!r}")


def handle_initiate_pairing(hass: HomeAssistant, call: ServiceCall) -> None:
    """Start pairing with a long-polling RM (CEM is the initiator)."""
    node_id_str: str = call.data["node_id"]
    pairing_token: str = call.data["pairing_token"]
    try:
        node_id = UUID(node_id_str)
    except ValueError as exc:
        raise ServiceValidationError(f"Invalid node_id {node_id_str!r}: {exc}") from exc

    for server in _iter_pairing_servers(hass):
        if server.initiate_pairing(node_id, pairing_token=pairing_token):
            _LOGGER.info("CEM-initiated pairing: requestPairing sent to node %s", node_id)
            return
    raise ServiceValidationError(f"Node {node_id_str!r} is not long-polling - is the RM running?")


async def handle_revoke_instruction(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service call to revoke a PEBC.Instruction sent to a paired RM node."""
    node_id_str: str = call.data["node_id"]
    try:
        node_id = UUID(node_id_str)
    except ValueError as exc:
        raise ServiceValidationError(f"Invalid node_id {node_id_str!r}: {exc}") from exc

    instruction_id_raw: str | None = call.data.get("instruction_id")
    if instruction_id_raw is not None:
        try:
            instruction_id = UUID(instruction_id_raw)
        except ValueError as exc:
            raise ServiceValidationError(f"Invalid instruction_id {instruction_id_raw!r}: {exc}") from exc
    else:
        pending_instructions = get_state(node_id).pending_instructions
        if not pending_instructions:
            raise ServiceValidationError(f"No pending instruction for node {node_id_str!r} — pass instruction_id explicitly")
        if len(pending_instructions) > 1:
            raise ServiceValidationError(
                f"Multiple pending instructions for node {node_id_str!r} — pass instruction_id explicitly"
            )
        instruction_id = pending_instructions[0].instruction_id

    for server in _iter_pairing_servers(hass):
        ws = server.active_connections.get(node_id)
        if ws is None:
            continue
        await send_revoke_instruction(ws, node_id_str, instruction_id)
        return

    raise ServiceValidationError(f"No active connection found for node {node_id_str!r}")


async def handle_send_instruction(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service call to send a PEBC.Instruction to a paired RM node."""
    node_id_str: str = call.data["node_id"]
    duration: int = call.data["duration"]
    raw_envelopes: list[dict] = call.data["power_envelopes"]
    abnormal_condition: bool = call.data.get("abnormal_condition", False)
    execution_time_raw: str | None = call.data.get("execution_time")

    try:
        node_id = UUID(node_id_str)
    except ValueError as exc:
        raise ServiceValidationError(f"Invalid node_id {node_id_str!r}: {exc}") from exc

    envelope_specs: list[PowerEnvelopeSpec] = []
    seen_quantities: set[CommodityQuantity] = set()
    for raw in raw_envelopes:
        try:
            cq = CommodityQuantity(raw["commodity_quantity"])
        except ValueError as exc:
            raise ServiceValidationError(f"Invalid commodity_quantity {raw['commodity_quantity']!r}: {exc}") from exc
        if cq in seen_quantities:
            raise ServiceValidationError(
                f"Duplicate commodity_quantity {cq.value!r} in power_envelopes. Each quantity must appear exactly once or not at all."
            )
        seen_quantities.add(cq)
        envelope_specs.append(PowerEnvelopeSpec(
            commodity_quantity=cq,
            upper_limit=float(raw["upper_limit"]),
            lower_limit=float(raw["lower_limit"]),
        ))

    state = get_state(node_id)
    if not state.power_constraints:
        raise ServiceValidationError(f"Node {node_id_str} has not sent any PowerConstraints yet — cannot instruct")

    for server in _iter_pairing_servers(hass):
        ws = server.active_connections.get(node_id)
        if ws is None:
            continue
        constraint = next(iter(state.power_constraints.values()))

        # Check all requested quantities are advertised by the RM.
        # The device sends one PowerConstraints message per phase, so we must collect them all
        # allowed_limit_ranges across all stored constraints, not just the first one.
        advertised = {r.commodity_quantity for c in state.power_constraints.values() for r in c.allowed_limit_ranges}
        unsupported = [spec for spec in envelope_specs if spec.commodity_quantity not in advertised]
        if unsupported:
            raise ServiceValidationError(
                f"Node {node_id_str} does not support {[s.commodity_quantity.value for s in unsupported]}. "
                f"Advertised quantities: {[q.value for q in advertised]}"
            )

        # Validate each envelope spec against the applicable allowed limit ranges.
        # Normal instruction: only validate against ranges with abnormal_condition_only = False.
        # Abnormal instruction: validate against ALL ranges (both True and False).
        #   An abnormal instruction only needs to satisfy at least one applicable range per quantity
        #   (the abnormal-only range acts as an extension of the base range).
        _validate_envelopes_against_constraints(envelope_specs, state.power_constraints.values(), abnormal_condition)

        await send_pebc_instruction(
            ws,
            str(node_id),
            envelope_specs,
            constraint,
            duration_ms=duration,
            abnormal_condition=abnormal_condition,
            execution_time=execution_time_raw,
        )
        return

    raise ServiceValidationError(f"No active connection found for node {node_id_str!r}")


def _is_value_valid(value, ranges):
    """True if value is within at least one applicable range.
    """
    if not ranges:
        return True

    return any(
        r.range_boundary.start_of_range <= value <= r.range_boundary.end_of_range
        for r in ranges
    )


def _filter_ranges(all_ranges, spec, limit_type, abnormal_condition):
    ranges = [r for r in all_ranges if r.commodity_quantity == spec.commodity_quantity and r.limit_type == limit_type]
    if not abnormal_condition:
        ranges = [r for r in ranges if not r.abnormal_condition_only]
    return ranges


def _range_error(spec, limit_type, value, ranges, abnormal_condition) -> ServiceValidationError:
    from s2python.pebc import PEBCPowerEnvelopeLimitType
    allowed = ", ".join(
        f"[{r.range_boundary.start_of_range}, {r.range_boundary.end_of_range}] W"
        f"{'(abnormal only)' if r.abnormal_condition_only else ''}"
        for r in ranges
    )
    direction = "upper" if limit_type == PEBCPowerEnvelopeLimitType.UPPER_LIMIT else "lower"
    mode = "(including abnormal-only)" if abnormal_condition else "(non-abnormal)"
    return ServiceValidationError(
        f"Requested {direction}_limit {value} W for {spec.commodity_quantity.value} is outside the "
        f"RM's allowed range(s): {allowed or 'none available'}. "
        f"The value must fall within at least one {mode} range."
    )


def _validate_envelopes_against_constraints(envelope_specs, constraints, abnormal_condition):
    """Validate requested instruction envelopes against RM's active constraints."""
    from s2python.pebc import PEBCPowerEnvelopeLimitType
    all_ranges = [r for c in constraints for r in c.allowed_limit_ranges]
    for spec in envelope_specs:
        for limit_type, value in [(PEBCPowerEnvelopeLimitType.UPPER_LIMIT, spec.upper_limit), (PEBCPowerEnvelopeLimitType.LOWER_LIMIT, spec.lower_limit)]:
            ranges = _filter_ranges(all_ranges, spec, limit_type, abnormal_condition)
            if not _is_value_valid(value, ranges):
                raise _range_error(spec, limit_type, value, ranges, abnormal_condition)