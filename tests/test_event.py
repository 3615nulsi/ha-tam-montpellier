"""Tests of the disruption events."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.tam_montpellier.const import (
    DEFAULT_ALERTS_URL,
    DEFAULT_GTFS_URL,
    DEFAULT_TRIP_UPDATES_URL,
    DEFAULT_VEHICLE_POSITIONS_URL,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .conftest import (
    build_alerts,
    build_gtfs,
    build_trip_updates,
    build_vehicle_positions,
    paris,
)
from .test_init import _entry

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "isolated_storage")

EVENT = "event.bravo_delta_tram_1_new_disruption"
WORKS = {
    "id": "works",
    "informed": [{"route_id": "1"}],
    "periods": [(paris(8, 0), paris(20, 0))],
    "header": "TR/L1/CODE",
    "description": "TRAVAUX : arrêt Bravo non desservi.",
}
OTHER_LINE = {"id": "bus", "informed": [{"route_id": "13"}], "description": "Bus"}


def _serve(aioclient_mock: AiohttpClientMocker, *alerts: dict) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(DEFAULT_GTFS_URL, content=build_gtfs())
    aioclient_mock.get(DEFAULT_TRIP_UPDATES_URL, content=build_trip_updates())
    aioclient_mock.get(DEFAULT_VEHICLE_POSITIONS_URL, content=build_vehicle_positions())
    aioclient_mock.get(DEFAULT_ALERTS_URL, content=build_alerts(*alerts))


async def test_new_alert_fires(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new alert on the line fires once; other lines do not."""
    freezer.move_to(paris(8, 12))
    _serve(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(EVENT).state == STATE_UNKNOWN

    _serve(aioclient_mock, OTHER_LINE)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(EVENT).state == STATE_UNKNOWN

    _serve(aioclient_mock, OTHER_LINE, WORKS)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(EVENT)
    fired = state.state
    assert fired != STATE_UNKNOWN
    assert state.attributes["event_type"] == "new_alert"
    assert state.attributes["title"] == "TR/L1/CODE"
    assert state.attributes["message"] == "TRAVAUX : arrêt Bravo non desservi."
    assert state.attributes["end"] == "2026-09-23T18:00:00+00:00"

    # Still in effect at the next update: not fired again.
    freezer.tick(60)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(EVENT).state == fired


async def test_alerts_in_effect_at_start_do_not_fire(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An alert already in effect when Home Assistant starts does not fire."""
    freezer.move_to(paris(8, 12))
    _serve(aioclient_mock, WORKS)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(EVENT).state == STATE_UNKNOWN
