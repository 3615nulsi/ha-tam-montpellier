"""Sensors exposing the next tram departures at the monitored stops."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry, TamCoordinator, stop_key_from_data
from .departures import Departure

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class TamSensorEntityDescription(SensorEntityDescription):
    """Describes a TaM departure sensor."""

    index: int
    """Index of the departure in the list of upcoming departures."""
    value_fn: Callable[[Departure], datetime | int]


def _minutes_until(departure: Departure) -> int:
    seconds = (departure.time - dt_util.now()).total_seconds()
    return max(0, math.floor(seconds / 60))


SENSOR_DESCRIPTIONS: tuple[TamSensorEntityDescription, ...] = (
    TamSensorEntityDescription(
        key="next_departure",
        translation_key="next_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=0,
        value_fn=lambda departure: departure.time,
    ),
    TamSensorEntityDescription(
        key="following_departure",
        translation_key="following_departure",
        device_class=SensorDeviceClass.TIMESTAMP,
        index=1,
        value_fn=lambda departure: departure.time,
    ),
    TamSensorEntityDescription(
        key="minutes_to_next_departure",
        translation_key="minutes_to_next_departure",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        index=0,
        value_fn=_minutes_until,
        entity_registry_enabled_default=False,
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


class TamDepartureSensor(CoordinatorEntity[TamCoordinator], SensorEntity):
    """A departure at a stop, for a line and direction."""

    entity_description: TamSensorEntityDescription
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"departures", "line_color", "line_text_color"})

    def __init__(
        self,
        coordinator: TamCoordinator,
        subentry: ConfigSubentry,
        description: TamSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._stop_key = stop_key_from_data(subentry.data)
        stop_id, route_id, direction_id = self._stop_key
        static = coordinator.static
        self._route = static.routes.get(route_id)
        line = self._route.short_name if self._route else route_id
        self._attr_unique_id = f"{stop_id}_{route_id}_{direction_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{stop_id}_{route_id}_{direction_id}")},
            name=subentry.title,
            manufacturer="TaM",
            model=f"Tram {line}",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _departures(self) -> list[Departure]:
        return self.coordinator.data.get(self._stop_key, [])

    @property
    def _departure(self) -> Departure | None:
        departures = self._departures
        index = self.entity_description.index
        return departures[index] if len(departures) > index else None

    @property
    def native_value(self) -> datetime | int | None:
        """Return the departure time (or minutes until departure)."""
        if (departure := self._departure) is None:
            return None
        return self.entity_description.value_fn(departure)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about the departure."""
        departure = self._departure
        attributes: dict[str, Any] = {
            "line": self._route.short_name if self._route else self._stop_key[1],
            "line_color": f"#{self._route.color}" if self._route else None,
            "line_text_color": f"#{self._route.text_color}" if self._route else None,
            "destination": departure.headsign if departure else None,
            "source": departure.source.value if departure else None,
            "delay": _delay_minutes(departure),
        }
        if self.entity_description.index == 0:
            attributes["departures"] = [
                {
                    "time": dep.time.isoformat(),
                    "minutes": _minutes_until(dep),
                    "destination": dep.headsign,
                    "source": dep.source.value,
                    "delay": _delay_minutes(dep),
                }
                for dep in self._departures
            ]
        return attributes


def _delay_minutes(departure: Departure | None) -> int | None:
    """Return the delay rounded to the minute, when known."""
    if departure is None or departure.delay is None:
        return None
    return round(departure.delay / 60)
