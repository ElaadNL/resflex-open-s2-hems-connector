# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for measurements handling (power measurements)."""
from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from s2python.common import CommodityQuantity, PowerForecast, PowerForecastElement, PowerForecastValue

from s2_cem_lib.const import MAX_FORECAST_ELEMENTS_PER_NODE
from s2_cem_lib.websocket.handlers.measurements import handle_power_forecast, handle_power_measurement
from s2_cem_lib.websocket.node_state import clear_state, get_state


def _make_forecast(start: datetime.datetime, count: int, expected_value: float) -> PowerForecast:
    elements = [
        PowerForecastElement(
            duration=3600_000,  # 1 hour in ms
            power_values=[PowerForecastValue(
                commodity_quantity=CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC,
                value_upper_limit=expected_value + 50.0,
                value_upper_95PPR=expected_value + 40.0,
                value_upper_68PPR=expected_value + 35.0,
                value_expected=expected_value,
                value_lower_68PPR=expected_value - 35.0,
                value_lower_95PPR=expected_value - 40.0,
                value_lower_limit=expected_value - 50.0,
            )],
        )
        for _ in range(count)
    ]
    return PowerForecast(message_id=uuid.uuid4(), start_time=start, elements=elements)


def test_handle_power_measurement_stores_value_in_state():
    """Check if power measurement is stored in HA state"""
    node_id = uuid.uuid4()
    quantity = "ELECTRIC.POWER.L1"
    pv = MagicMock(commodity_quantity=quantity, value=1576.5)
    measurement = MagicMock(values=[pv])
    handle_power_measurement(MagicMock(), "remote", node_id, measurement)
    assert get_state(node_id).power_measurements[quantity] == 1576.5
    clear_state(node_id)


def test_handle_forecast_stored_in_state():
    """Check if power forecast is stored in HA state with correct times. We never touch the actual PowerForecastElement objects."""
    node_id = uuid.uuid4()
    clear_state(node_id)

    now = datetime.datetime.now(datetime.UTC)
    forecast_start = now + datetime.timedelta(hours=1)
    
    expected_value = 3232.234342

    handle_power_forecast(MagicMock(), "remote", node_id, _make_forecast(forecast_start, 3, expected_value))
    assert len(get_state(node_id).forecast_elements) == 3
    assert get_state(node_id).forecast_elements[0].start_time == forecast_start
    assert get_state(node_id).forecast_elements[0].end_time == forecast_start + datetime.timedelta(hours=1)
    assert get_state(node_id).forecast_elements[1].start_time == forecast_start + datetime.timedelta(hours=1)
    assert get_state(node_id).forecast_elements[1].end_time == forecast_start + datetime.timedelta(hours=2)
    assert get_state(node_id).forecast_elements[2].start_time == forecast_start + datetime.timedelta(hours=2)
    assert get_state(node_id).forecast_elements[2].end_time == forecast_start + datetime.timedelta(hours=3)

    # Check that the PowerForecastElement objects are not touched at all
    assert get_state(node_id).forecast_elements[0].element is not None
    assert get_state(node_id).forecast_elements[0].element.duration.root == 3600_000
    assert get_state(node_id).forecast_elements[0].element.power_values[0].commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_expected == expected_value
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_upper_limit == expected_value + 50.0
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_upper_95PPR == expected_value + 40.0
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_upper_68PPR == expected_value + 35.0
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_lower_68PPR == expected_value - 35.0
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_lower_95PPR == expected_value - 40.0
    assert get_state(node_id).forecast_elements[0].element.power_values[0].value_lower_limit == expected_value - 50.0



def test_handle_power_forecast_merges_and_drops_superseded_elements():
    """
    Two forecasts arrive for the same node.

    This test verifies that when a new forecast overlaps only the tail end of an existing one.
    The overlapping part should be correctly dropped and replaced by the new forecast data.

    Test is as follows:
        1. First, we have a 3 hour forecast.
        2. Then we receive a second 2 hour forecast that starts one hour later. Therefore there is a 
              two hour overlap with the first forecast.
        3. The logic should keep the first hour of the first forecast, but drop the last two hours
              of the first forecast and replace it with the new forecast data.
             """
    node_id = uuid.uuid4()
    clear_state(node_id)

    now = datetime.datetime.now(datetime.UTC)
    forecast_1 = _make_forecast(now + datetime.timedelta(hours=1), 3, expected_value=6666.66666) # starts now, lasts 3 hours
    handle_power_forecast(MagicMock(), "remote", node_id, forecast_1)
    assert len(get_state(node_id).forecast_elements) == 3

    forecast_2 = _make_forecast(now + datetime.timedelta(hours=2), 2, expected_value=3232.234342) # start 1 hour from now, lasts 2 hours
    handle_power_forecast(MagicMock(), "remote", node_id, forecast_2)

    elements = get_state(node_id).forecast_elements
    assert len(elements) == 3

    # First element is the surviving one from forecast 1
    assert elements[0].start_time == forecast_1.start_time
    assert elements[0].end_time == forecast_1.start_time + datetime.timedelta(hours=1)

    # Remaining two come from forecast 2
    assert elements[1].start_time == forecast_2.start_time
    assert elements[2].start_time == forecast_2.start_time + datetime.timedelta(hours=1)


    assert elements[0].element.power_values[0].value_expected == 6666.66666


    clear_state(node_id)


def test_power_forecast_elements_are_capped():
    """Accumulated forecast elements are capped, keeping the nearest-term ones."""
    node_id = uuid.uuid4()
    clear_state(node_id)

    now = datetime.datetime.now(datetime.UTC)
    start = now + datetime.timedelta(hours=1)

    # Fresh non-overlapping horizons so nothing supersedes, and the schema caps one forecast at 288 elements.
    for index in range(4):
        handle_power_forecast(
            MagicMock(), "remote", node_id,
            _make_forecast(start + datetime.timedelta(hours=index * 288), 288, expected_value=1000.0),
        )

    elements = get_state(node_id).forecast_elements
    assert len(elements) == MAX_FORECAST_ELEMENTS_PER_NODE

    # The nearest-term elements survive and the furthest-term tail is what gets dropped.
    assert elements[0].start_time == start
    assert elements[-1].end_time == start + datetime.timedelta(hours=MAX_FORECAST_ELEMENTS_PER_NODE)
    clear_state(node_id)