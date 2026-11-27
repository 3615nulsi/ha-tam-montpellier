"""Sensors exposing the next tram departures at the monitored stops.

Each line also has a sensor counting its trams in service.

Minutes are always rounded down, so that "2 min" guarantees at least two
minutes before the tram: someone leaving when the sensor says they have time
arrives early rather than late. The minutes sensor is rewritten at the exact
instant its value changes instead of waiting for the next poll.

The timestamp sensors give the predicted time rounded down to 15 seconds:
the operator's prediction moves by a few seconds from one poll to the next,
and each move would otherwise be a new state recorded in the database. The
minutes and the ``departures`` attribute keep the exact time. Note that the
Home Assistant frontend displays timestamps as "in X minutes" rounded to the
nearest minute and refreshed once a minute, which may overestimate the
remaining time by up to 1.5 minutes; the minutes sensor is the one to display.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfTime
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_ROUTE_ID, CONF_WALK_MINUTES, SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry, TamCoordinator
from .departures import Departure, current_service_day, service_day_bounds
from .entity import TamLineEntity, TamStopEntity
from .fleet import fleet_lines
from .vehicles import MODELS, VehicleSize, vehicle_model

PARALLEL_UPDATES = 0

# Refresh slightly after a boundary so the new value is already reached.
_BOUNDARY_EPSILON = timedelta(milliseconds=50)
# Step of the times given by the timestamp sensors.
TIME_STATE_STEP = timedelta(seconds=15)


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
    # In UTC: two datetimes of the same zone subtract by wall clock, off by an
    # hour across a daylight saving change.
    seconds = (dt_util.as_utc(departure.time) - dt_util.as_utc(now)).total_seconds()
    return max(0, math.floor(seconds / 60))


def next_minute_change(departure: Departure, now: datetime) -> datetime:
    """Return when ``minutes_until`` of a departure next decreases.

    With ``n`` whole minutes left, the count drops to ``n - 1`` once less
    than ``n`` minutes remain, i.e. at ``time - n minutes``. With less than
    a minute left, the next change is the departure itself.

    The arithmetic is done in UTC: on an aware local datetime, subtracting a
    timedelta moves the wall clock, which is off by an hour when a daylight
    saving change lies between now and the departure. The result would then
    be in the past, and the refresh would fire again at once, endlessly.
    """
    departure_utc = dt_util.as_utc(departure.time)
    now_utc = dt_util.as_utc(now)
    minutes = math.floor((departure_utc - now_utc).total_seconds() / 60)
    change = departure_utc - timedelta(minutes=max(minutes, 0)) + _BOUNDARY_EPSILON
    # Never schedule a refresh in the past: that would loop on the event loop.
    return max(change, now_utc + _BOUNDARY_EPSILON)


def rounded_time(departure: Departure, _now: datetime) -> datetime:
    """Return the departure time rounded down to ``TIME_STATE_STEP``.

    Down rather than to the nearest: as with the minutes, the tram is not
    announced later than predicted.
    """
    timestamp = departure.time.timestamp()
    step = TIME_STATE_STEP.total_seconds()
    return datetime.fromtimestamp(timestamp - timestamp % step, departure.time.tzinfo)


SENSOR_DESCRIPTIONS: tuple[TamSensorEntityDescription, ...] = (
    TamSensorEntityDescription(
        key="next_departure",
        translation_key="next_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=0,
        value_fn=rounded_time,
    ),
    TamSensorEntityDescription(
        key="following_departure",
        translation_key="following_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=1,
        value_fn=rounded_time,
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


NEXT_VEHICLE_DESCRIPTION = TamSensorEntityDescription(
    key="next_vehicle",
    translation_key="next_vehicle",
    device_class=SensorDeviceClass.ENUM,
    options=[model.key for model in MODELS],
    index=0,
    value_fn=lambda departure, _now: (
        model.key if (model := vehicle_model(departure.vehicle_id)) else None
    ),
)


MINUTES_TO_LEAVE_DESCRIPTION = TamSensorEntityDescription(
    key="minutes_to_leave",
    translation_key="minutes_to_leave",
    native_unit_of_measurement=UnitOfTime.MINUTES,
    index=0,
    value_fn=minutes_until,
    minute_precision=True,
)


FIRST_DEPARTURE_DESCRIPTION = SensorEntityDescription(
    key="first_departure",
    translation_key="first_departure",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_registry_enabled_default=False,
)
LAST_DEPARTURE_DESCRIPTION = SensorEntityDescription(
    key="last_departure",
    translation_key="last_departure",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_registry_enabled_default=False,
)


ALERT_MESSAGE_DESCRIPTION = SensorEntityDescription(
    key="alert_message",
    translation_key="alert_message",
)

VEHICLES_DESCRIPTION = SensorEntityDescription(
    key="vehicles_in_service",
    translation_key="vehicles_in_service",
    state_class=SensorStateClass.MEASUREMENT,
)

# Home Assistant rejects states longer than this.
_MAX_STATE_LENGTH = 255
_ALERT_SEPARATOR = " • "
_NO_ALERT = {"fr": "Aucune perturbation", "en": "No disruption"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of each line and of each monitored stop."""
    coordinator = entry.runtime_data
    async_add_entities(
        TamVehiclesSensor(coordinator, line, VEHICLES_DESCRIPTION)
        for line in fleet_lines(coordinator.static)
    )
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        entities: list[SensorEntity] = [
            TamDepartureSensor(coordinator, subentry, description)
            for description in SENSOR_DESCRIPTIONS
        ]
        route = coordinator.static.routes.get(subentry.data[CONF_ROUTE_ID])
        if route is not None and route.is_tram:
            # Bus fleet numbers do not tell their model.
            entities.append(
                TamDepartureSensor(coordinator, subentry, NEXT_VEHICLE_DESCRIPTION)
            )
        entities.append(
            TamAlertMessageSensor(coordinator, subentry, ALERT_MESSAGE_DESCRIPTION)
        )
        if CONF_WALK_MINUTES in subentry.data:
            entities.append(
                TamLeaveSensor(coordinator, subentry, MINUTES_TO_LEAVE_DESCRIPTION)
            )
        entities.extend(
            TamServiceDaySensor(coordinator, subentry, description)
            for description in (FIRST_DEPARTURE_DESCRIPTION, LAST_DEPARTURE_DESCRIPTION)
        )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class TamDepartureSensor(TamStopEntity, SensorEntity):
    """A departure at a stop, for a line and direction."""

    entity_description: TamSensorEntityDescription
    _unrecorded_attributes = frozenset(
        {"departures", "line_color", "line_text_color", "stop_name", "direction"}
    )

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
            **_vehicle_attributes(departure),
        }
        if self._clockwise is not None:
            attributes["clockwise"] = self._clockwise
        if self.entity_description.minute_precision:
            # Read by the card, whatever the names given to the device.
            attributes["stop_name"] = self._stop_name
            attributes["direction"] = self._direction
            # Kept up to date at each minute boundary, see _schedule_refresh.
            attributes["departures"] = [
                departure_attributes(dep, now) for dep in departures
            ]
        return attributes


class TamServiceDaySensor(TamStopEntity, SensorEntity):
    """First or last scheduled departure of the service day at the stop.

    From the timetables: a last tram after midnight belongs to the evening
    before, and is shown until it has left.
    """

    _bounds: tuple[object, date, tuple[datetime, datetime] | None] | None = None

    @property
    def native_value(self) -> datetime | None:
        """Return the first or the last departure of the day."""
        static = self.coordinator.static
        day = current_service_day(static, self._stop_key, dt_util.utcnow())
        # Cached for the day: the timetables of a stop hold many trips.
        # Compared by identity: StaticData equality would compare every trip.
        if (
            self._bounds is None
            or self._bounds[0] is not static
            or self._bounds[1] != day
        ):
            self._bounds = (
                static,
                day,
                service_day_bounds(static, self._stop_key, day),
            )
        if (bounds := self._bounds[2]) is None:
            return None
        return (
            bounds[0] if self.entity_description.key == "first_departure" else bounds[1]
        )


class TamLeaveSensor(TamDepartureSensor):
    """Minutes left before leaving for the next tram within reach.

    The tram within reach is the first one leaving after the walk to the
    stop; the count is rounded down, and rewritten at each minute boundary,
    as the minutes to the next departure.
    """

    _unrecorded_attributes = frozenset({"line_color", "line_text_color"})

    def _departures(self, now: datetime) -> list[Departure]:
        """Return the departures within reach, at the time to leave."""
        return self._leave_departures(now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the tram to catch and the walk to the stop."""
        departure = self._departure(self._departures(dt_util.utcnow()))
        walk = self._walk or timedelta()
        return {
            "line": self._line,
            "line_color": f"#{self._route.color}" if self._route else None,
            "line_text_color": f"#{self._route.text_color}" if self._route else None,
            "walk_minutes": int(walk.total_seconds() // 60),
            "leave_at": departure.time.isoformat() if departure else None,
            "departure": ((departure.time + walk).isoformat() if departure else None),
            "destination": departure.headsign if departure else None,
        }


def departure_attributes(departure: Departure, now: datetime) -> dict[str, Any]:
    """Return a departure as listed in the ``departures`` attribute."""
    return {
        "time": departure.time.isoformat(),
        "minutes": minutes_until(departure, now),
        "destination": departure.headsign,
        "source": departure.source.value,
        "delay": _delay_minutes(departure),
        **_vehicle_attributes(departure),
    }


def _vehicle_attributes(departure: Departure | None) -> dict[str, Any]:
    """Return the tram of a departure: fleet number, model and length."""
    vehicle_id = departure.vehicle_id if departure else None
    model = vehicle_model(vehicle_id)
    return {
        "vehicle": vehicle_id,
        "vehicle_model": model.name if model else None,
        "vehicle_size": model.size.value if model else None,
        "vehicle_length": model.length if model else None,
        "vehicle_confirmed": departure.vehicle_confirmed if vehicle_id else None,
    }


def _delay_minutes(departure: Departure | None) -> int | None:
    """Return the delay rounded to the minute, when known."""
    if departure is None or departure.delay is None:
        return None
    return round(departure.delay / 60)


class TamAlertMessageSensor(TamStopEntity, SensorEntity):
    """Text of the service alerts in effect for the stop.

    States are limited to 255 characters: longer texts are truncated in the
    state and given in full in the ``full_message`` attribute.
    """

    _unrecorded_attributes = frozenset({"full_message"})

    def _message(self) -> str | None:
        alerts = self._active_alerts()
        if not alerts:
            return None
        return _ALERT_SEPARATOR.join(alert.description for alert in alerts)

    @property
    def icon(self) -> str | None:
        """Show a checked message when the service runs normally.

        The state is free text, so icons.json cannot map icons to states.
        """
        return None if self._active_alerts() else "mdi:message-check-outline"

    @property
    def native_value(self) -> str:
        """Return the alert text, or a "no disruption" text."""
        if (message := self._message()) is None:
            language = self.hass.config.language.split("-")[0]
            return _NO_ALERT.get(language, _NO_ALERT["en"])
        if len(message) > _MAX_STATE_LENGTH:
            return message[: _MAX_STATE_LENGTH - 1].rstrip() + "…"
        return message

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the untruncated text and the number of alerts."""
        return {
            "line": self._line,
            "alert_count": len(self._active_alerts()),
            "full_message": self._message(),
        }


class TamVehiclesSensor(TamLineEntity, SensorEntity):
    """Number of trams in service on a line.

    Trams are counted for a while after they were last seen in the real-time
    feeds, see the fleet module; ``running`` is the instant count.
    """

    _unrecorded_attributes = frozenset({"vehicles", "line_color", "line_text_color"})

    @property
    def available(self) -> bool:
        """Return whether a feed naming the trams answered."""
        return super().available and self.coordinator.fleet_available

    @property
    def native_value(self) -> int:
        """Return the number of trams in service."""
        return len(self.coordinator.fleet.in_service(self._fleet_line))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the line, the instant count and the fleet numbers."""
        fleet = self.coordinator.fleet
        vehicles = fleet.in_service(self._fleet_line)
        return {
            "line": self._line,
            "line_color": f"#{self._route.color}" if self._route else None,
            "line_text_color": f"#{self._route.text_color}" if self._route else None,
            "running": fleet.running(self._fleet_line),
            "vehicles": vehicles,
            # Lets the card show tram lengths only where both kinds run.
            "short_vehicles": sum(
                1
                for vehicle_id in vehicles
                if (model := vehicle_model(vehicle_id))
                and model.size is VehicleSize.SHORT
            ),
        }
