"""The TaM Montpellier tramway integration."""

from __future__ import annotations

from datetime import datetime
import logging
import zipfile

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN, GTFS_REFRESH_HOUR, GTFS_REFRESH_MINUTE
from .coordinator import GtfsDownloadError, TamConfigEntry, TamCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Errors raised while loading an unusable GTFS archive.
_GTFS_ERRORS = (GtfsDownloadError, zipfile.BadZipFile, KeyError, ValueError)


async def async_setup_entry(hass: HomeAssistant, entry: TamConfigEntry) -> bool:
    """Set up TaM Montpellier from a config entry."""
    coordinator = TamCoordinator(hass, entry)
    try:
        await coordinator.async_load_static()
    except _GTFS_ERRORS as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="gtfs_unavailable",
            translation_placeholders={"error": str(err)},
        ) from err
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _async_refresh_static(_now: datetime) -> None:
        """Reload the GTFS archive, published daily by the operator."""
        try:
            await coordinator.async_load_static(force_download=True)
        except _GTFS_ERRORS as err:
            _LOGGER.warning("Could not reload the TaM GTFS archive: %s", err)
            return
        await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _async_refresh_static,
            hour=GTFS_REFRESH_HOUR,
            minute=GTFS_REFRESH_MINUTE,
            second=0,
        )
    )
    # Stops are config subentries: reload to pick up added or removed stops.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: TamConfigEntry) -> None:
    """Reload the entry when its subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TamConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
