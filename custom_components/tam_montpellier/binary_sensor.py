"""Service disruption binary sensors of the monitored stops."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .alerts import Alert, alerts_for
from .const import SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry
from .entity import TamStopEntity

PARALLEL_UPDATES = 0

DISRUPTION_DESCRIPTION = BinarySensorEntityDescription(
    key="disruption",
    translation_key="disruption",
    device_class=BinarySensorDeviceClass.PROBLEM,
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
        async_add_entities(
            [TamDisruptionSensor(coordinator, subentry, DISRUPTION_DESCRIPTION)],
            config_subentry_id=subentry.subentry_id,
        )


class TamDisruptionSensor(TamStopEntity, BinarySensorEntity):
    """On when a service alert concerns the stop, its line or the network."""

    _unrecorded_attributes = frozenset({"alerts"})

    @property
    def _alerts(self) -> list[Alert]:
        return alerts_for(self.coordinator.alerts, self._stop_key, dt_util.utcnow())

    @property
    def is_on(self) -> bool:
        """Return whether the service is disrupted."""
        return bool(self._alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the active alerts."""
        now = dt_util.utcnow()
        alerts = self._alerts
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
