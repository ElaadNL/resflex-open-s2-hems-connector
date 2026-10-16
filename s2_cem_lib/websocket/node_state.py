# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Per-node PEBC runtime state.

Each connected RM gets a PEBCNodeState instance that holds the constraints
it has sent.  Handlers read/write this.  State persists across WS
reconnects (cleared only on unpair or platform restart).

The ``event_bus`` and ``scheduler`` fields are injected by the WS view
from S2CemApp so that handlers can publish telemetry and spawn retry
tasks without touching platform-specific APIs.
"""
from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from s2python.common import CommodityQuantity, PowerForecastElement
from s2python.pebc import PEBCEnergyConstraint, PEBCPowerConstraints

if TYPE_CHECKING:
    from s2python.common import ResourceManagerDetails

    from ..s2_cem.ports import EventBus, TaskScheduler

# Registry of PEBCNodeState instances keyed by node_id.
# Each connected RM gets its own node state with the constraints it has
# advertised and the instructions the CEM has sent.
_registry: dict[UUID, PEBCNodeState] = {}


@dataclass
class PowerEnvelopeSpec:
    """A single per-quantity power limit for a PEBC.Instruction."""
    commodity_quantity: CommodityQuantity
    upper_limit: float
    lower_limit: float


@dataclass
class TimestampedForecastElement:
    """A PowerForecastElement with its absolute start and end times.

    Elements are kept sorted chronologically.
    """
    start_time: datetime.datetime
    end_time: datetime.datetime
    element: PowerForecastElement


@dataclass
class PendingInstruction:
    """Parameters of an instruction sent to this node's RM, kept for retry on:
        - InstructionStatus = ABORTED/REJECTED
        - ReceptionStatus = TEMPORARY_ERROR.
       execution_time marks when the instruction window starts and together with duration_ms it defines the time span.
       Instructions with non-overlapping time windows are able to be retrying simultaneously.
       message_id is the WS message_id used to correlate ReceptionStatus responses.
    """
    envelopes: list[PowerEnvelopeSpec]
    duration_ms: int
    constraint: PEBCPowerConstraints
    instruction_id: UUID
    message_id: UUID
    execution_time: datetime.datetime
    retry_count: int = 0
    abnormal_condition: bool = False


@dataclass
class PEBCNodeState:
    """Mutable PEBC state for a single connected RM node."""

    event_bus: EventBus | None = None
    scheduler: TaskScheduler | None = None

    rm_details: ResourceManagerDetails | None = None
    
    power_measurements: dict[str, float] = field(default_factory=dict)

    # PowerConstraints, bounded by MAX_POWER_CONSTRAINTS_PER_NODE
    power_constraints: dict[UUID, PEBCPowerConstraints] = field(default_factory=dict)

    # EnergyConstraints, bounded by MAX_ENERGY_CONSTRAINTS_PER_NODE
    energy_constraints: dict[UUID, PEBCEnergyConstraint] = field(default_factory=dict)

    # Instructions sent to this node that are still active 
    # (still executing and waiting response or being retried).
    # Multiple non-overlapping instructions can be pending at the same time.
    pending_instructions: list[PendingInstruction] = field(default_factory=list)

    # All outgoing messages tracked for TEMPORARY_ERROR as we need to retry if this ReceptionStatus is received.
    # Maps message_id to raw_json, retry count and pending task.
    # pending_task is the scheduled retry, which is cancelleed and replaced on each new TEMPORARY_ERROR.
    # This is for the same message so that only one retry is ever currently on its way per message.
    # Entries are removed on OK, PERMANENT_ERROR, or INVALID type messages.
    sent_messages: dict[UUID, tuple[str, int, asyncio.Task[None] | None]] = field(default_factory=dict)

    # Merged, cleaned forecast elements from RM PowerForecast messages.
    # Sorted chronologically. past-elements and superseded (overlapping) elements are pruned on each new forecast.
    # We always want to store the newest forecast data.
    # Bounded by MAX_FORECAST_ELEMENTS_PER_NODE, furthest elements dropped first.
    forecast_elements: list[TimestampedForecastElement] = field(default_factory=list)


Constraint = PEBCPowerConstraints | PEBCEnergyConstraint


def prune_expired_constraints(constraints: dict[UUID, Constraint]) -> int:
    """Drop constraints whose valid_until has passed. Returns how many were removed."""
    now = datetime.datetime.now(datetime.UTC)
    expired = [cid for cid, c in constraints.items() if c.valid_until is not None and c.valid_until < now]
    for cid in expired:
        del constraints[cid]
    return len(expired)


def get_state(node_id: UUID) -> PEBCNodeState:
    """Return the PEBC state for *node_id*, creating it if it does not exist."""
    if node_id not in _registry:
        _registry[node_id] = PEBCNodeState()
    return _registry[node_id]


def clear_state(node_id: UUID) -> None:
    """Remove all PEBC state for *node_id* from the registry."""
    _registry.pop(node_id, None)
