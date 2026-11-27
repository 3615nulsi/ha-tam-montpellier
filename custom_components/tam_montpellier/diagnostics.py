"""Diagnostics of the TaM Montpellier integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DEFAULT_URLS, DOMAIN, SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry
from .fleet import fleet_line_name, fleet_lines


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TamConfigEntry
) -> dict[str, Any]:
    """Return the state of the integration, for bug reports.

    The TaM addresses are public; addresses set by the user may be private
    and are redacted.
    """
    coordinator = entry.runtime_data
    static = coordinator.static
    integration = await async_get_integration(hass, DOMAIN)
    custom_urls = {
        key for key, default in DEFAULT_URLS.items() if entry.data.get(key) != default
    }
    archive = await hass.async_add_executor_job(coordinator.archive_info)
    today = dt_util.now(static.timezone).date()
    return {
        "version": str(integration.version),
        "data": async_redact_data(dict(entry.data), custom_urls),
        "options": dict(entry.options),
        "stops": [
            {"title": subentry.title, **subentry.data}
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_STOP
        ],
        "gtfs": {
            "downloaded": archive[0].isoformat() if archive else None,
            "size": archive[1] if archive else None,
            "lines": len(static.routes),
            "trips": len(static.trips),
            "services_today": len(static.services_on(today)),
        },
        "feeds": {
            **coordinator.feeds_available,
            "last_trip_update": (
                coordinator.feed_timestamp.isoformat()
                if coordinator.feed_timestamp
                else None
            ),
        },
        "alerts": len(coordinator.alerts),
        "fleet": {
            "available": coordinator.fleet_available,
            "in_service": {
                fleet_line_name(static, line): len(coordinator.fleet.in_service(line))
                for line in fleet_lines(static)
            },
        },
    }
