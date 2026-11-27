"""Service disruption binary sensors of the monitored stops."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_WALK_MINUTES, SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry
from .entity import TamStopEntity
from .leave import is_time_to_leave, leave_window_changes

PARALLEL_UPDATES = 0

# Refresh slightly after a boundary so the new value is already reached.
_EPSILON = timedelta(milliseconds=50)

DISRUPTION_DESCRIPTION = BinarySensorEntityDescription(
    key="disruption",
    translation_key="disruption",
    device_class=BinarySensorDeviceClass.PROBLEM,
)

TIME_TO_LEAVE_DESCRIPTION = BinarySensorEntityDescription(
    key="time_to_leave",
    translation_key="time_to_leave",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the disruption sensor of each monitored stop."""
    coordinator = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        entities: list[BinarySensorEntity] = [
            TamDisruptionSensor(coordinator, subentry, DISRUPTION_DESCRIPTION)
        ]
        if CONF_WALK_MINUTES in subentry.data:
            entities.append(
                TamTimeToLeaveSensor(coordinator, subentry, TIME_TO_LEAVE_DESCRIPTION)
            )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class TamDisruptionSensor(TamStopEntity, BinarySensorEntity):
    """On when a service alert concerns the stop, its line or the network."""

    _unrecorded_attributes = frozenset({"alerts"})

    @property
    def is_on(self) -> bool:
        """Return whether the service is disrupted."""
        return bool(self._active_alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the active alerts."""
        now = dt_util.utcnow()
        alerts = self._active_alerts()
        return {
            "line": self._line,
            "message": alerts[0].description if alerts else None,
            "alerts": [
                {
                    "message": alert.description,
                    "title": alert.header,
                    "end": end.isoformat() if (end := alert.current_end(now)) else None,
                    "url": alert.url,
                }
                for alert in alerts
            ],
        }


class TamTimeToLeaveSensor(TamStopEntity, BinarySensorEntity):
    """On during the set window before leaving for the next tram within reach.

    Rewritten when the window opens and closes, not only at each poll.
    """

    _unsub_refresh: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Schedule the first local refresh."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_refresh)
        self._schedule_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the new data and reschedule the local refresh."""
        super()._handle_coordinator_update()
        self._schedule_refresh()

    @callback
    def _cancel_refresh(self) -> None:
        if self._unsub_refresh is not None:
            self._unsub_refresh()
            self._unsub_refresh = None

    @callback
    def _schedule_refresh(self) -> None:
        self._cancel_refresh()
        now = dt_util.utcnow()
        if (leave := self._leave_time(now)) is None:
            return
        if (change := leave_window_changes(leave, now, self._leave_window)) is None:
            return
        self._unsub_refresh = async_track_point_in_utc_time(
            self.hass, self._async_refresh, change + _EPSILON
        )

    @callback
    def _async_refresh(self, _now: datetime) -> None:
        self._unsub_refresh = None
        self.async_write_ha_state()
        self._schedule_refresh()

    def _leave_time(self, now: datetime) -> datetime | None:
        leaving = self._leave_departures(now)
        return leaving[0].time if leaving else None

    @property
    def is_on(self) -> bool:
        """Return whether it is time to leave for the stop."""
        now = dt_util.utcnow()
        leave = self._leave_time(now)
        return leave is not None and is_time_to_leave(leave, now, self._leave_window)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when to leave, and the window."""
        leave = self._leave_time(dt_util.utcnow())
        return {
            "line": self._line,
            "leave_at": leave.isoformat() if leave else None,
            "window_minutes": int(self._leave_window.total_seconds() // 60),
        }
