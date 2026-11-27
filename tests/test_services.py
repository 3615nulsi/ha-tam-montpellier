"""Tests of the get_departures action."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.tam_montpellier.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from .conftest import find_device, paris
from .test_init import MINUTES_SENSOR, _entry, mock_feeds_fixture  # noqa: F401

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "isolated_storage")


async def _set_up(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, **data) -> dict:
    return await hass.services.async_call(
        DOMAIN, "get_departures", data, blocking=True, return_response=True
    )


async def test_get_departures_by_device(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The departures of a stop device are those of its minutes sensor."""
    freezer.move_to(paris(8, 12))
    await _set_up(hass)
    device = find_device(hass, "B_1_0")

    response = await _call(hass, device_id=device.id)
    expected = hass.states.get(MINUTES_SENSOR).attributes["departures"]
    assert response["departures"] == expected

    response = await _call(hass, device_id=device.id, count=2)
    assert response["departures"] == expected[:2]


async def test_get_departures_by_ids(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Beyond the six of the sensor, more departures can be asked for."""
    freezer.move_to(paris(8, 12))
    await _set_up(hass)
    response = await _call(hass, stop_id="B", route_id="1", direction_id=0, count=20)
    departures = response["departures"]
    assert len(departures) > 6
    assert departures[0]["time"] == paris(8, 14, 30).isoformat()
    assert departures[0]["source"] == "realtime"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({}, "stop_required"),
        ({"stop_id": "C", "route_id": "1", "direction_id": 0}, "stop_not_followed"),
        ({"device_id": "unknown"}, "stop_not_followed"),
    ],
)
async def test_get_departures_errors(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    data: dict,
    error: str,
) -> None:
    """A missing or unfollowed stop is reported."""
    freezer.move_to(paris(8, 12))
    await _set_up(hass)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, **data)
    assert err.value.translation_key == error


async def test_get_departures_not_loaded(hass: HomeAssistant) -> None:
    """The action is registered with the integration, before any entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, stop_id="B", route_id="1", direction_id=0)
    assert err.value.translation_key == "not_loaded"
