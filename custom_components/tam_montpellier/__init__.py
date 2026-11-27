"""The TaM Montpellier tramway integration."""

from __future__ import annotations

from datetime import datetime
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_WALK_MINUTES,
    DOMAIN,
    GTFS_REFRESH_HOUR,
    GTFS_REFRESH_MINUTE,
    GTFS_REFRESH_SPREAD,
    SUBENTRY_TYPE_STOP,
)
from .coordinator import (
    GTFS_ERRORS,
    GtfsDownloadError,
    TamConfigEntry,
    TamCoordinator,
    stop_key_from_data,
)
from .entity import line_device_id
from .fleet import fleet_lines
from .issues import async_delete_issues, async_update_issues
from .labels import async_get_labels
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The Lovelace card shipped with the integration.
CARD_URL = f"/{DOMAIN}/tam-board-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "tam-board-card.js"

# Set once the card is loaded as an extra module, which cannot be undone.
_EXTRA_MODULE_LOADED = f"{DOMAIN}_card_extra_module"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the departure board card and register the actions."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    async_setup_services(hass)
    return True


async def _async_load_card(hass: HomeAssistant) -> None:
    """Load the card in the frontend.

    Done with each setup of the entry, not once per start of Home Assistant:
    the resource is removed with the integration, and must come back when the
    integration is added again.
    """
    # The card is cached by browsers and apps; the version in its URL makes
    # them fetch the new one when the integration is updated.
    integration = await async_get_integration(hass, DOMAIN)
    url = f"{CARD_URL}?v={integration.version}"
    if await _async_declare_card(hass, url):
        return
    if "frontend" in hass.config.components and not hass.data.get(_EXTRA_MODULE_LOADED):
        add_extra_js_url(hass, url)
        hass.data[_EXTRA_MODULE_LOADED] = True


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
    await _async_load_card(hass)
    coordinator = TamCoordinator(hass, entry)
    coordinator.labels = await async_get_labels(hass)
    try:
        await _async_load_static(coordinator)
    except GTFS_ERRORS as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="gtfs_unavailable",
            translation_placeholders={"error": str(err)},
        ) from err
    await async_update_issues(hass, entry, coordinator)
    _remove_stale_line_devices(hass, entry, coordinator)
    _remove_leave_entities(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _async_refresh_static(_now: datetime) -> None:
        """Reload the GTFS archive, published daily by the operator."""
        lines = _lines(coordinator)
        try:
            await coordinator.async_load_static(force_download=True)
        except GTFS_ERRORS as err:
            _LOGGER.warning("Could not reload the TaM GTFS archive: %s", err)
            return
        if _lines(coordinator) != lines:
            # Entities are made from the lines: a new or a renamed line needs
            # them made again.
            _LOGGER.info("The TaM lines changed, reloading the integration")
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        await async_update_issues(hass, entry, coordinator)
        await coordinator.async_request_refresh()

    hour, minute, second = gtfs_refresh_time(entry.entry_id)
    entry.async_on_unload(
        async_track_time_change(
            hass, _async_refresh_static, hour=hour, minute=minute, second=second
        )
    )
    # Stops are config subentries: reload to pick up added or removed stops.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _lines(coordinator: TamCoordinator) -> tuple[set[str], dict[tuple[str, int], str]]:
    """Return what the entities are made from: the lines and their variants."""
    return set(coordinator.static.routes), dict(coordinator.static.variants)


def _remove_stale_line_devices(
    hass: HomeAssistant, entry: TamConfigEntry, coordinator: TamCoordinator
) -> None:
    """Remove the devices of the lines no longer in the timetables.

    Stop devices are left alone: their sensors stay, and a repair issue
    tells about a stop or a line gone.
    """
    current = {line_device_id(line) for line in fleet_lines(coordinator.static)}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        identifiers = {key for domain, key in device.identifiers if domain == DOMAIN}
        lines = {key for key in identifiers if key.startswith("line_")}
        if lines and not lines & current:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


def _remove_leave_entities(hass: HomeAssistant, entry: TamConfigEntry) -> None:
    """Remove the "time to leave" entities of the stops without a walk time."""
    registry = er.async_get(hass)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        if CONF_WALK_MINUTES in subentry.data:
            continue
        stop_id, route_id, direction_id = stop_key_from_data(subentry.data)
        for platform, key in (
            (Platform.SENSOR, "minutes_to_leave"),
            (Platform.BINARY_SENSOR, "time_to_leave"),
        ):
            unique_id = f"{stop_id}_{route_id}_{direction_id}_{key}"
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, unique_id):
                registry.async_remove(entity_id)


def gtfs_refresh_time(entry_id: str) -> tuple[int, int, int]:
    """Return the time of the nightly download, the same for an installation.

    Spread over an hour from one installation to another, so that they do not
    all download the archive from the TaM servers at once.
    """
    digest = hashlib.sha256(entry_id.encode()).digest()
    offset = int.from_bytes(digest[:4]) % int(GTFS_REFRESH_SPREAD.total_seconds())
    seconds = GTFS_REFRESH_HOUR * 3600 + GTFS_REFRESH_MINUTE * 60 + offset
    hour, rest = divmod(seconds, 3600)
    return hour, *divmod(rest, 60)


async def _async_load_static(coordinator: TamCoordinator) -> None:
    """Load the timetables, downloading them again if the cached ones fail."""
    try:
        await coordinator.async_load_static()
    except GtfsDownloadError:
        raise  # Nothing cached, and the download just failed.
    except GTFS_ERRORS as err:
        _LOGGER.warning(
            "Could not read the cached TaM GTFS archive, downloading it again: %r",
            err,
        )
        # Not conditional: the server would tell the broken archive is current.
        await coordinator.async_load_static(force_download=True, conditional=False)


async def _async_update_listener(hass: HomeAssistant, entry: TamConfigEntry) -> None:
    """Reload the entry when its subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TamConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: TamConfigEntry) -> None:
    """Remove the card from the dashboard resources with the integration."""
    async_delete_issues(hass)
    try:
        if (resources := await _async_stored_resources(hass)) is None:
            return
        for item in _card_resources(resources):
            await resources.async_delete_item(item["id"])
    except Exception:
        # The resources API is internal to Home Assistant: should it change,
        # the integration must still be removable.
        _LOGGER.warning(
            "Could not remove the TaM card from the dashboard resources",
            exc_info=True,
        )
