"""The TaM Montpellier tramway integration."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
import zipfile

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, GTFS_REFRESH_HOUR, GTFS_REFRESH_MINUTE
from .coordinator import GtfsDownloadError, TamConfigEntry, TamCoordinator

if TYPE_CHECKING:
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The Lovelace card shipped with the integration.
CARD_URL = f"/{DOMAIN}/tam-board-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "tam-board-card.js"

# Errors raised while loading an unusable GTFS archive.
_GTFS_ERRORS = (GtfsDownloadError, zipfile.BadZipFile, KeyError, ValueError)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the departure board card and load it in the frontend."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    # The card is cached by browsers and apps; the version in its URL makes
    # them fetch the new one when the integration is updated.
    integration = await async_get_integration(hass, DOMAIN)
    url = f"{CARD_URL}?v={integration.version}"
    declared = await _async_declare_card(hass, url)
    if not declared and "frontend" in hass.config.components:
        add_extra_js_url(hass, url)
    return True


async def _async_declare_card(hass: HomeAssistant, url: str) -> bool:
    """Declare the card as a dashboard resource, when they are stored by HA.

    The frontend loads extra modules alongside its own code: one that runs
    first defines the card in a registry the frontend then replaces, and a
    page loaded before this integration is set up misses the card. Both show
    "Erreur de configuration" until the page is reloaded
    (home-assistant/frontend#53890). Dashboard resources are loaded by the
    frontend code itself, and are known as soon as Home Assistant starts.
    """
    try:
        if (resources := await _async_stored_resources(hass)) is None:
            return False
        items = _card_resources(resources)
        if not items:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif items[0]["url"] != url:
            await resources.async_update_item(items[0]["id"], {"url": url})
        for item in items[1:]:
            await resources.async_delete_item(item["id"])
    except Exception:
        # The resources API is internal to Home Assistant: should it change,
        # the card falls back to an extra module.
        _LOGGER.warning(
            "Could not declare the TaM card as a dashboard resource", exc_info=True
        )
        return False
    return True


async def _async_stored_resources(
    hass: HomeAssistant,
) -> ResourceStorageCollection | None:
    """Return the dashboard resources, unless they are set in YAML."""
    # Read without importing the dashboards integration, whose internals may
    # change: the integration must keep loading if they do.
    lovelace = hass.data.get("lovelace")
    if lovelace is None or lovelace.resource_mode != "storage":
        return None
    # Loaded on first use only: changing them before would overwrite them.
    await lovelace.resources.async_get_info()
    return lovelace.resources


def _card_resources(resources: ResourceStorageCollection) -> list[dict[str, Any]]:
    """Return the dashboard resources loading the card, whatever its version."""
    return [
        item
        for item in resources.async_items()
        if item["url"].partition("?")[0] == CARD_URL
    ]


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


async def async_remove_entry(hass: HomeAssistant, entry: TamConfigEntry) -> None:
    """Remove the card from the dashboard resources with the integration."""
    if (resources := await _async_stored_resources(hass)) is None:
        return
    for item in _card_resources(resources):
        await resources.async_delete_item(item["id"])
