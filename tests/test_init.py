"""Tests of the config flows, setup and sensors."""

from __future__ import annotations

from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.tam_montpellier.const import (
    CONF_DIRECTION_ID,
    CONF_GTFS_URL,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    CONF_TRIP_UPDATES_URL,
    DEFAULT_GTFS_URL,
    DEFAULT_TRIP_UPDATES_URL,
    DOMAIN,
    SUBENTRY_TYPE_STOP,
)
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from .conftest import build_gtfs, build_trip_updates, paris, trip_id

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "isolated_storage")

ENTRY_DATA = {
    CONF_TRIP_UPDATES_URL: DEFAULT_TRIP_UPDATES_URL,
    CONF_GTFS_URL: DEFAULT_GTFS_URL,
}
STOP_DATA = {CONF_ROUTE_ID: "1", CONF_DIRECTION_ID: 0, CONF_STOP_ID: "B"}
NEXT_SENSOR = "sensor.bravo_delta_tram_1_next_departure"


@pytest.fixture(name="mock_feeds")
def mock_feeds_fixture(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve the synthetic feeds."""
    aioclient_mock.get(DEFAULT_GTFS_URL, content=build_gtfs())
    aioclient_mock.get(
        DEFAULT_TRIP_UPDATES_URL,
        content=build_trip_updates(
            {
                "trip_id": trip_id(1),
                "stops": [
                    ("A", paris(8, 11), 60, False),
                    ("B", paris(8, 14, 30), 90, False),
                ],
            }
        ),
    )
    return aioclient_mock


def _gtfs_downloads(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(str(call[1]) == DEFAULT_GTFS_URL for call in aioclient_mock.mock_calls)


def _entry(with_stop: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="TaM Montpellier",
        data=ENTRY_DATA,
        subentries_data=(
            [
                ConfigSubentryData(
                    data=STOP_DATA,
                    subentry_type=SUBENTRY_TYPE_STOP,
                    title="Bravo → Delta (Tram 1)",
                    unique_id="B_1_0",
                )
            ]
            if with_stop
            else []
        ),
    )


async def test_user_flow(hass: HomeAssistant, mock_feeds: AiohttpClientMocker) -> None:
    """The user flow checks the real-time feed and creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA


async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Connection and format errors are reported."""
    aioclient_mock.get(DEFAULT_TRIP_UPDATES_URL, exc=ClientError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(DEFAULT_TRIP_UPDATES_URL, content=b"not a protobuf \xff\xff")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["errors"] == {"base": "invalid_feed"}


async def test_sensors(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
) -> None:
    """Sensors expose the upcoming departures of the stop."""
    freezer.move_to(paris(8, 12))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(NEXT_SENSOR)
    assert state is not None
    assert state.state == "2026-09-23T06:14:30+00:00"
    assert state.attributes["destination"] == "Delta"
    assert state.attributes["source"] == "realtime"
    assert state.attributes["delay"] == 2
    assert state.attributes["line"] == "1"
    assert state.attributes["line_color"] == "#005CA9"
    departures = state.attributes["departures"]
    assert [dep["source"] for dep in departures[:2]] == ["realtime", "scheduled"]
    assert departures[1]["minutes"] == 11

    following = hass.states.get("sensor.bravo_delta_tram_1_following_departure")
    assert following is not None
    assert following.state == "2026-09-23T06:23:00+00:00"

    minutes = entity_registry.async_get(
        "sensor.bravo_delta_tram_1_minutes_to_next_departure"
    )
    assert minutes is not None
    assert minutes.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # Once the tram has left, the next one becomes the next departure.
    freezer.move_to(paris(8, 16))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(NEXT_SENSOR).state == "2026-09-23T06:23:00+00:00"


async def test_realtime_outage_falls_back_to_schedule(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Scheduled times are used when the real-time feed is down."""
    freezer.move_to(paris(8, 12))
    aioclient_mock.get(DEFAULT_GTFS_URL, content=build_gtfs())
    aioclient_mock.get(DEFAULT_TRIP_UPDATES_URL, exc=ClientError())
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(NEXT_SENSOR)
    assert state.state == "2026-09-23T06:13:00+00:00"
    assert state.attributes["source"] == "scheduled"


async def test_gtfs_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Setup is retried while the timetables cannot be downloaded."""
    aioclient_mock.get(DEFAULT_GTFS_URL, exc=ClientError())
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_stop_subentry_flow(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A stop is added by choosing the line, the direction and the stop."""
    freezer.move_to(paris(8, 12))
    entry = _entry(with_stop=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_STOP), context={"source": "user"}
    )
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ROUTE_ID].config["options"]
    assert [option["value"] for option in options] == ["1"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROUTE_ID: "1"}
    )
    assert result["step_id"] == "direction"
    options = result["data_schema"].schema[CONF_DIRECTION_ID].config["options"]
    assert options[0] == {"value": "0", "label": "Vers Delta (depuis Alpha)"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DIRECTION_ID: "0"}
    )
    assert result["step_id"] == "stop"
    options = result["data_schema"].schema[CONF_STOP_ID].config["options"]
    # The terminus of the direction is not offered.
    assert [option["value"] for option in options] == ["A", "B", "C"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "B"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bravo → Delta (Tram 1)"
    await hass.async_block_till_done()

    # The entry is reloaded and the sensors of the new stop are created.
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(NEXT_SENSOR) is not None

    # Adding the same stop again is refused.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_STOP), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ROUTE_ID: "1"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DIRECTION_ID: "0"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STOP_ID: "B"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_gtfs_cache_reused(
    hass: HomeAssistant,
    mock_feeds: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The GTFS archive is downloaded once, then refreshed nightly."""
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(paris(8, 12))
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _gtfs_downloads(mock_feeds) == 1

    freezer.move_to(paris(3, 30, day=24))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _gtfs_downloads(mock_feeds) == 2
    assert entry.state is ConfigEntryState.LOADED
