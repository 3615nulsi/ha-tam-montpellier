"""Base entity for the TaM Montpellier integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .alerts import Alert, alerts_for
from .const import ATTRIBUTION, DOMAIN
from .coordinator import TamCoordinator, stop_key_from_data


class TamStopEntity(CoordinatorEntity[TamCoordinator]):
    """An entity of a monitored stop, for a line and direction."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TamCoordinator,
        subentry: ConfigSubentry,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._stop_key = stop_key_from_data(subentry.data)
        stop_id, route_id, direction_id = self._stop_key
        self._route = coordinator.static.routes.get(route_id)
        self._line = self._route.short_name if self._route else route_id
        self._attr_unique_id = f"{stop_id}_{route_id}_{direction_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{stop_id}_{route_id}_{direction_id}")},
            name=subentry.title,
            manufacturer="TaM",
            model=f"Tram {self._line}",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _active_alerts(self) -> list[Alert]:
        """Return the alerts in effect for this stop, line and direction."""
        return alerts_for(self.coordinator.alerts, self._stop_key, dt_util.utcnow())
