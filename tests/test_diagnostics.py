"""Tests of the diagnostics."""

from __future__ import annotations

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.tam_montpellier.const import (
    CONF_DIRECTION_ID,
    CONF_GTFS_URL,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    DEFAULT_GTFS_URL,
    DEFAULT_URLS,
    DOMAIN,
    SUBENTRY_TYPE_STOP,
)
from custom_components.tam_montpellier.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .conftest import build_gtfs, paris
from .test_init import _serve_feeds

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "isolated_storage")

STOP_DATA = {CONF_ROUTE_ID: "1", CONF_DIRECTION_ID: 0, CONF_STOP_ID: "B"}
PRIVATE_GTFS_URL = "https://user:secret@example.org/GTFS.zip"


async def _set_up(hass: HomeAssistant, data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TaM Montpellier",
        data=data,
        subentries_data=[
            ConfigSubentryData(
                data=STOP_DATA,
                subentry_type=SUBENTRY_TYPE_STOP,
                title="Bravo → Delta (Tram 1)",
                unique_id="B_1_0",
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_diagnostics(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The diagnostics tell the version, the stops, the archive and the feeds."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    entry = await _set_up(hass, dict(DEFAULT_URLS))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    integration = await async_get_integration(hass, DOMAIN)
    assert diagnostics["version"] == str(integration.version)
    # The default TaM addresses are public: kept.
    assert diagnostics["data"] == DEFAULT_URLS
    assert diagnostics["stops"] == [{"title": "Bravo → Delta (Tram 1)", **STOP_DATA}]
    gtfs = diagnostics["gtfs"]
    assert gtfs["size"] == len(build_gtfs())
    assert gtfs["downloaded"] is not None
    assert gtfs["lines"] == 1
    assert gtfs["services_today"] >= 1
    feeds = diagnostics["feeds"]
    assert datetime.fromisoformat(feeds.pop("last_trip_update")) == paris(8, 0)
    assert feeds == {"trip_updates": True, "alerts": True, "vehicle_positions": True}
    assert diagnostics["alerts"] == 0
    assert diagnostics["fleet"]["available"] is True
    assert diagnostics["fleet"]["in_service"] == {"1": 0}


async def test_diagnostics_redact_custom_urls(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An address set by the user may hold credentials: it is redacted."""
    freezer.move_to(paris(8, 12))
    aioclient_mock.get(PRIVATE_GTFS_URL, content=build_gtfs())
    _serve_feeds(aioclient_mock)
    entry = await _set_up(hass, {**DEFAULT_URLS, CONF_GTFS_URL: PRIVATE_GTFS_URL})

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["data"][CONF_GTFS_URL] == "**REDACTED**"
    assert PRIVATE_GTFS_URL not in str(diagnostics)
    assert DEFAULT_GTFS_URL not in str(diagnostics["data"])
