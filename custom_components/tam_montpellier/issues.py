"""Repair issues raised when the timetables no longer match the setup.

Issues are created when a GTFS archive is loaded, and deleted as soon as a
later archive or a change of the followed stops fixes them.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import CONF_ROUTE_ID, CONF_STOP_ID, DOMAIN, SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry, TamCoordinator

# An archive older than this was not refreshed for several nights.
GTFS_STALE_AGE = timedelta(days=3)

ISSUE_GTFS_STALE = "gtfs_stale"
_STOP_MISSING = "stop_missing_"
_ROUTE_MISSING = "route_missing_"


async def async_update_issues(
    hass: HomeAssistant, entry: TamConfigEntry, coordinator: TamCoordinator
) -> None:
    """Create the issues of the loaded archive, delete the solved ones."""
    static = coordinator.static
    wanted: dict[str, tuple[str, dict[str, str]]] = {}

    archive = await hass.async_add_executor_job(coordinator.archive_info)
    today = dt_util.now(static.timezone).date()
    no_service = not static.services_on(today) and not static.services_on(
        today + timedelta(days=1)
    )
    if no_service or (
        archive is not None and dt_util.utcnow() - archive[0] > GTFS_STALE_AGE
    ):
        downloaded = (
            archive[0].astimezone(static.timezone).date().isoformat()
            if archive
            else "?"
        )
        wanted[ISSUE_GTFS_STALE] = (ISSUE_GTFS_STALE, {"date": downloaded})

    missing_routes: dict[str, list[str]] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        route_id = subentry.data[CONF_ROUTE_ID]
        if route_id not in static.routes:
            missing_routes.setdefault(route_id, []).append(subentry.title)
        elif subentry.data[CONF_STOP_ID] not in static.stop_names:
            wanted[f"{_STOP_MISSING}{subentry.subentry_id}"] = (
                "stop_missing",
                {"stop": subentry.title},
            )
    for route_id, stops in missing_routes.items():
        wanted[f"{_ROUTE_MISSING}{route_id}"] = (
            "route_missing",
            {"line": route_id, "stops": ", ".join(sorted(stops))},
        )

    for issue_id, (translation_key, placeholders) in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_delete_issues(hass: HomeAssistant) -> None:
    """Delete all the issues of the integration, when it is removed."""
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
