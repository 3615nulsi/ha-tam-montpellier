"""Tests of the repair issues."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.tam_montpellier.const import (
    CONF_DIRECTION_ID,
    CONF_EXTRA_ROUTES,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    DEFAULT_URLS,
    DOMAIN,
    SUBENTRY_TYPE_STOP,
)
from custom_components.tam_montpellier.coordinator import TamCoordinator
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import paris
from .test_init import _serve_feeds

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "isolated_storage")


def _stop(stop_id: str, route_id: str = "1", title: str = "") -> ConfigSubentryData:
    return ConfigSubentryData(
        data={CONF_ROUTE_ID: route_id, CONF_DIRECTION_ID: 0, CONF_STOP_ID: stop_id},
        subentry_type=SUBENTRY_TYPE_STOP,
        title=title or f"{stop_id} (Tram {route_id})",
        unique_id=f"{stop_id}_{route_id}_0",
    )


async def _set_up(
    hass: HomeAssistant, *stops: ConfigSubentryData, options: dict | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TaM Montpellier",
        data=dict(DEFAULT_URLS),
        options=options or {},
        subentries_data=list(stops),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _issues(hass: HomeAssistant) -> set[str]:
    return {
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN
    }


async def test_no_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A current archive serving the followed stop raises no issue."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    await _set_up(hass, _stop("B"))
    assert _issues(hass) == set()


async def test_old_archive(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An archive not refreshed for days raises an issue, gone once refreshed."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    with patch.object(
        TamCoordinator,
        "archive_info",
        return_value=(paris(8, 12) - timedelta(days=4), 1000),
    ):
        entry = await _set_up(hass, _stop("B"))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "gtfs_stale")
    assert issue is not None
    assert issue.translation_placeholders == {"date": "2026-09-19"}

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass) == set()


async def test_no_service(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An archive without any service today or tomorrow raises an issue."""
    freezer.move_to(paris(8, 12, day=26))
    _serve_feeds(aioclient_mock)
    await _set_up(hass, _stop("B"))
    assert _issues(hass) == {"gtfs_stale"}


async def test_stop_missing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A followed stop gone from the archive raises an issue until removed."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    entry = await _set_up(hass, _stop("B"), _stop("Z", title="Zulu (Tram 1)"))
    gone = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.data[CONF_STOP_ID] == "Z"
    )
    issue_id = f"stop_missing_{gone.subentry_id}"
    assert _issues(hass) == {issue_id}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.translation_key == "stop_missing"
    assert issue.translation_placeholders == {"stop": "Zulu (Tram 1)"}

    assert hass.config_entries.async_remove_subentry(entry, gone.subentry_id)
    await hass.async_block_till_done()
    assert _issues(hass) == set()


async def test_route_missing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A followed line gone from the archive raises one issue for its stops."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    await _set_up(
        hass,
        _stop("X", "99", "Bus stop (Bus 99)"),
        options={CONF_EXTRA_ROUTES: ["99"]},
    )
    assert _issues(hass) == {"route_missing_99"}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "route_missing_99")
    assert issue.translation_placeholders == {
        "line": "99",
        "stops": "Bus stop (Bus 99)",
    }


async def test_issues_deleted_with_integration(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Removing the integration removes its issues."""
    freezer.move_to(paris(8, 12))
    _serve_feeds(aioclient_mock)
    entry = await _set_up(hass, _stop("Z"))
    assert _issues(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass) == set()
