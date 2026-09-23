"""Sensors exposing the next tram departures at the monitored stops.

Minutes are always rounded down, so that "2 min" guarantees at least two
minutes before the tram: someone leaving when the sensor says they have time
arrives early rather than late. The minutes sensor is rewritten at the exact
instant its value changes instead of waiting for the next poll.

The timestamp sensors keep the exact predicted time. Note that the Home
Assistant frontend displays them as "in X minutes" rounded to the nearest
minute and refreshed once a minute, which may overestimate the remaining time
by up to 1.5 minutes; the minutes sensor is the one to display.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfTime
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry, TamCoordinator
from .departures import Departure
from .entity import TamStopEntity

PARALLEL_UPDATES = 0

# Refresh slightly after a boundary so the new value is already reached.
_BOUNDARY_EPSILON = timedelta(milliseconds=50)


@dataclass(frozen=True, kw_only=True)
class TamSensorEntityDescription(SensorEntityDescription):
    """Describes a TaM departure sensor."""

    index: int
    """Index of the departure in the list of upcoming departures."""
    value_fn: Callable[[Departure, datetime], datetime | int | str]
    minute_precision: bool = False
    """Rewrite the state each time a displayed minute count changes."""


def minutes_until(departure: Departure, now: datetime) -> int:
    """Return the whole minutes left before a departure, rounded down."""
    seconds = (departure.time - now).total_seconds()
    return max(0, math.floor(seconds / 60))


def next_minute_change(departure: Departure, now: datetime) -> datetime:
    """Return when ``minutes_until`` of a departure next decreases.

    With ``n`` whole minutes left, the count drops to ``n - 1`` once less
    than ``n`` minutes remain, i.e. at ``time - n minutes``. With less than
    a minute left, the next change is the departure itself.
    """
    minutes = math.floor((departure.time - now).total_seconds() / 60)
    return departure.time - timedelta(minutes=max(minutes, 0)) + _BOUNDARY_EPSILON


SENSOR_DESCRIPTIONS: tuple[TamSensorEntityDescription, ...] = (
    TamSensorEntityDescription(
        key="next_departure",
        translation_key="next_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=0,
        value_fn=lambda departure, _now: departure.time,
    ),
    TamSensorEntityDescription(
        key="following_departure",
        translation_key="following_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=1,
        value_fn=lambda departure, _now: departure.time,
    ),
    TamSensorEntityDescription(
        key="next_destination",
        translation_key="next_destination",
        index=0,
        value_fn=lambda departure, _now: departure.headsign,
    ),
    TamSensorEntityDescription(
        key="minutes_to_next_departure",
        translation_key="minutes_to_next_departure",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        index=0,
        value_fn=minutes_until,
        minute_precision=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of each monitored stop."""
    coordinator = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        async_add_entities(
            (
                TamDepartureSensor(coordinator, subentry, description)
                for description in SENSOR_DESCRIPTIONS
            ),
            config_subentry_id=subentry.subentry_id,
        )


class TamDepartureSensor(TamStopEntity, SensorEntity):
    """A departure at a stop, for a line and direction."""

    entity_description: TamSensorEntityDescription
    _unrecorded_attributes = frozenset({"departures", "line_color", "line_text_color"})

    def __init__(
        self,
        coordinator: TamCoordinator,
        subentry: ConfigSubentry,
        description: TamSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, subentry, description)
        self._unsub_refresh: CALLBACK_TYPE | None = None

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
        """Rewrite the state when it changes between two polls.

        Departed trams are dropped at their departure time, and the minutes
        sensor is refreshed at each minute boundary of its departures.
        """
        self._cancel_refresh()
        now = dt_util.utcnow()
        departures = self._departures(now)
        if self.entity_description.minute_precision:
            changes = [next_minute_change(dep, now) for dep in departures]
        else:
            departure = self._departure(departures)
            changes = [departure.time + _BOUNDARY_EPSILON] if departure else []
        if changes:
            self._unsub_refresh = async_track_point_in_utc_time(
                self.hass, self._async_refresh, min(changes)
            )

    @callback
    def _async_refresh(self, _now: datetime) -> None:
        self._unsub_refresh = None
        self.async_write_ha_state()
        self._schedule_refresh()

    def _departures(self, now: datetime) -> list[Departure]:
        """Return the upcoming departures, without the trams already gone."""
        return [
            dep
            for dep in self.coordinator.data.get(self._stop_key, [])
            if dep.time > now
        ]

    def _departure(self, departures: list[Departure]) -> Departure | None:
        index = self.entity_description.index
        return departures[index] if len(departures) > index else None

    @property
    def native_value(self) -> datetime | int | str | None:
        """Return the departure time, minutes until departure or destination."""
        now = dt_util.utcnow()
        if (departure := self._departure(self._departures(now))) is None:
            return None
        return self.entity_description.value_fn(departure, now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about the departure."""
        now = dt_util.utcnow()
        departures = self._departures(now)
        departure = self._departure(departures)
        attributes: dict[str, Any] = {
            "line": self._line,
            "line_color": f"#{self._route.color}" if self._route else None,
            "line_text_color": f"#{self._route.text_color}" if self._route else None,
            "destination": departure.headsign if departure else None,
            "source": departure.source.value if departure else None,
            "delay": _delay_minutes(departure),
        }
        if self.entity_description.minute_precision:
            # Kept up to date at each minute boundary, see _schedule_refresh.
            attributes["departures"] = [
                {
                    "time": dep.time.isoformat(),
                    "minutes": minutes_until(dep, now),
                    "destination": dep.headsign,
                    "source": dep.source.value,
                    "delay": _delay_minutes(dep),
                }
                for dep in departures
            ]
        return attributes


def _delay_minutes(departure: Departure | None) -> int | None:
    """Return the delay rounded to the minute, when known."""
    if departure is None or departure.delay is None:
        return None
    return round(departure.delay / 60)
