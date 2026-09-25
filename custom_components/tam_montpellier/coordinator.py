"""Data update coordinator for the TaM Montpellier integration."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from aiohttp import ClientError, ClientTimeout
from google.protobuf.message import DecodeError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .alerts import Alert, parse_alerts
from .const import (
    CONF_ALERTS_URL,
    CONF_DIRECTION_ID,
    CONF_GTFS_URL,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    CONF_TRIP_UPDATES_URL,
    DEFAULT_ALERTS_URL,
    DOMAIN,
    GTFS_DOWNLOAD_TIMEOUT,
    GTFS_MAX_AGE,
    MAX_DEPARTURES,
    REQUEST_TIMEOUT,
    SUBENTRY_TYPE_STOP,
    UPDATE_INTERVAL,
)
from .departures import (
    Departure,
    RealtimeSnapshot,
    compute_departures,
    parse_trip_updates,
)
from .gtfs_static import StaticData, StopKey, load_static

_LOGGER = logging.getLogger(__name__)

type TamConfigEntry = ConfigEntry[TamCoordinator]


class GtfsDownloadError(Exception):
    """Raised when the static GTFS archive cannot be obtained."""


def stop_key_from_data(data: dict) -> StopKey:
    """Build the stop key of a stop subentry."""
    return (data[CONF_STOP_ID], data[CONF_ROUTE_ID], int(data[CONF_DIRECTION_ID]))


async def async_fetch_feed(hass: HomeAssistant, url: str) -> bytes:
    """Download a GTFS-RT feed."""
    session = async_get_clientsession(hass)
    async with session.get(url, timeout=ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
        resp.raise_for_status()
        return await resp.read()


class TamCoordinator(DataUpdateCoordinator[dict[StopKey, list[Departure]]]):
    """Fetch real-time data and compute the departures of the monitored stops."""

    config_entry: TamConfigEntry
    static: StaticData

    def __init__(self, hass: HomeAssistant, entry: TamConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._gtfs_path = Path(hass.config.path(STORAGE_DIR, DOMAIN, "gtfs.zip"))
        self._static_cache_path = self._gtfs_path.with_name("gtfs_static.pickle")
        self._realtime_available = True
        self._alerts_available = True
        self.alerts: list[Alert] = []
        """Last known service alerts, kept when the alert feed fails."""
        self.stop_keys: set[StopKey] = {
            stop_key_from_data(subentry.data)
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_STOP
        }

    async def async_load_static(self, force_download: bool = False) -> None:
        """Download (when outdated) and parse the static GTFS archive.

        The parsed archive is cached, so a restart does not parse it again.
        """
        await self._async_download_gtfs(force_download)
        self.static = await self.hass.async_add_executor_job(
            load_static,
            self._gtfs_path,
            self._static_cache_path,
            {key[0] for key in self.stop_keys},
        )
        _LOGGER.debug(
            "Loaded %d tram lines and %d trips from GTFS",
            len(self.static.routes),
            len(self.static.trips),
        )

    async def _async_download_gtfs(self, force: bool) -> None:
        """Refresh the cached GTFS archive, keeping the old one on failure."""
        mtime = await self.hass.async_add_executor_job(_file_mtime, self._gtfs_path)
        if not force and mtime is not None and dt_util.utcnow() - mtime < GTFS_MAX_AGE:
            return
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                self.config_entry.data[CONF_GTFS_URL],
                timeout=ClientTimeout(total=GTFS_DOWNLOAD_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.read()
        except (ClientError, TimeoutError) as err:
            if mtime is None:
                raise GtfsDownloadError(str(err)) from err
            _LOGGER.warning("Could not refresh the TaM GTFS archive: %s", err)
            return
        await self.hass.async_add_executor_job(_write_file, self._gtfs_path, payload)

    async def _async_update_data(self) -> dict[StopKey, list[Departure]]:
        """Fetch the real-time feed and compute departures.

        When the real-time feed is unavailable, departures fall back to the
        scheduled times instead of making the entities unavailable.
        """
        await self._async_update_alerts()
        snapshot = RealtimeSnapshot()
        try:
            payload = await async_fetch_feed(
                self.hass, self.config_entry.data[CONF_TRIP_UPDATES_URL]
            )
            snapshot = await self.hass.async_add_executor_job(
                parse_trip_updates, payload, set(self.static.routes)
            )
        except (ClientError, TimeoutError, DecodeError) as err:
            self._set_realtime_available(False, err)
        else:
            self._set_realtime_available(True, None)
        return await self.hass.async_add_executor_job(
            self._compute_departures, snapshot, dt_util.now()
        )

    async def _async_update_alerts(self) -> None:
        """Refresh the service alerts, keeping the last ones on failure."""
        url = self.config_entry.data.get(CONF_ALERTS_URL, DEFAULT_ALERTS_URL)
        try:
            payload = await async_fetch_feed(self.hass, url)
            self.alerts = await self.hass.async_add_executor_job(parse_alerts, payload)
        except (ClientError, TimeoutError, DecodeError) as err:
            if self._alerts_available:
                _LOGGER.warning("TaM alert feed unavailable: %s", err)
                self._alerts_available = False
            return
        if not self._alerts_available:
            _LOGGER.info("TaM alert feed is available again")
            self._alerts_available = True

    def _compute_departures(
        self, snapshot: RealtimeSnapshot, now: datetime
    ) -> dict[StopKey, list[Departure]]:
        return {
            key: compute_departures(self.static, snapshot, key, now, MAX_DEPARTURES)
            for key in self.stop_keys
        }

    def _set_realtime_available(self, available: bool, err: Exception | None) -> None:
        """Log real-time feed outages once; scheduled times are used meanwhile."""
        if available == self._realtime_available:
            return
        self._realtime_available = available
        if available:
            _LOGGER.info("TaM real-time feed is available again")
        else:
            _LOGGER.warning(
                "TaM real-time feed unavailable, using scheduled times: %s", err
            )


def _file_mtime(path: Path) -> datetime | None:
    try:
        return dt_util.utc_from_timestamp(path.stat().st_mtime)
    except FileNotFoundError:
        return None


def _write_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
