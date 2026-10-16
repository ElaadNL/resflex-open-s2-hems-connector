# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Handles PowerMeasurement and PowerForecast (RM -> CEM)."""
from __future__ import annotations

import datetime
import logging

from aiohttp import web
from s2python.common import PowerForecast, PowerMeasurement

from ...const import MAX_FORECAST_ELEMENTS_PER_NODE, SIGNAL_POWER_MEASUREMENT_FMT
from ..node_state import TimestampedForecastElement, get_state

_LOGGER = logging.getLogger(__name__)


def handle_power_measurement(
    _ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    measurement: PowerMeasurement,
) -> bool:
    """
    RM reports its current power draw/production.
    Published through the event bus for external consumers.

    Flow:
      RM -> CEM: PowerMeasurement  (periodic, or on change)

    Example: https://docs.s2standard.org/docs/examples/pv/#rm---cem-powermeasurement
    """
    _LOGGER.debug(
        "PowerMeasurement from %s (node %s): timestamp=%s, values=%s",
        remote, node_id, measurement.measurement_timestamp, measurement.values,
    )
    state = get_state(node_id)

    # we signal that a new measurement has arrived using our defined format
    signal = SIGNAL_POWER_MEASUREMENT_FMT.format(node_id=node_id) 
    for pv in measurement.values:
        state.power_measurements[pv.commodity_quantity] = pv.value
        if state.event_bus is not None:
            state.event_bus.send(
                signal, pv.commodity_quantity, pv.value
            )
    return False


def handle_power_forecast(
    _ws: web.WebSocketResponse,
    remote: str,
    node_id: str,
    forecast: PowerForecast,
) -> None:
    """
    RM sends a probabilistic power forecast to the CEM.
    This is completely optional for the RM to send.
    The CEM stores these forecasts and could use them to make instruction decisions.

    The storing of forecast data works as follows:
      1. Delete any old forecast data (end_time < now) thats no longer future.
      2. Delete any data that overlaps with the incoming forecast data. New data always takes precedence.
      3. Add all new forecast data that is in the future.
      4. Cap the result at MAX_FORECAST_ELEMENTS_PER_NODE, dropping the furthest elements first.

    Goal is to always keep most updated forecast data whilst cleaning out old data.

    Note that we wrap the PowerForecastElements with their start and end times, but we DO NOT modify
    the actual PowerForecastElement objects at all.
    """
    now = datetime.datetime.now(datetime.UTC)

    # Convert incoming elements to timestamped form.
    new_timestamped: list[TimestampedForecastElement] = []
    cursor = forecast.start_time
    for element in forecast.elements:
        duration_ms: int = element.duration.root
        end = cursor + datetime.timedelta(milliseconds=duration_ms)
        new_timestamped.append(TimestampedForecastElement(
            start_time=cursor,
            end_time=end,
            element=element,
        ))
        cursor = end

    state = get_state(node_id)

    # Keep only elements that are still upcoming and NOT superseded by the new forecast.
    state.forecast_elements = [
        e for e in state.forecast_elements
        if e.end_time >= now and e.start_time < forecast.start_time
    ]

    # Append incoming elements that are still in the future.
    state.forecast_elements.extend(
        e for e in new_timestamped if e.end_time >= now
    )

    # Cap the merged list, dropping the furthest elements first.
    if len(state.forecast_elements) > MAX_FORECAST_ELEMENTS_PER_NODE:
        dropped = len(state.forecast_elements) - MAX_FORECAST_ELEMENTS_PER_NODE
        del state.forecast_elements[MAX_FORECAST_ELEMENTS_PER_NODE:]
        _LOGGER.warning("PowerForecast from %s (node %s): dropped %d furthest element(s), cap is %d", remote, node_id, dropped, MAX_FORECAST_ELEMENTS_PER_NODE)

    _LOGGER.debug(
        "PowerForecast from %s (node %s): start=%s, %d new element(s), %d total after merge",
        remote, node_id, forecast.start_time, len(new_timestamped), len(state.forecast_elements),
    )

