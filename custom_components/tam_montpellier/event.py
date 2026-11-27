"""Events fired when a service alert starts to concern a monitored stop."""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SUBENTRY_TYPE_STOP
from .coordinator import TamConfigEntry, TamCoordinator
from .entity import TamStopEntity

PARALLEL_UPDATES = 0

EVENT_NEW_ALERT = "new_alert"

NEW_DISRUPTION_DESCRIPTION = EventEntityDescription(
    key="new_disruption",
    translation_key="new_disruption",
    event_types=[EVENT_NEW_ALERT],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the disruption event of each monitored stop."""
    coordinator = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STOP:
            continue
        async_add_entities(
            [TamDisruptionEvent(coordinator, subentry, NEW_DISRUPTION_DESCRIPTION)],
            config_subentry_id=subentry.subentry_id,
        )


class TamDisruptionEvent(TamStopEntity, EventEntity):
    """Fired once for each new alert concerning the stop, its line or network.

    Alerts already in effect when Home Assistant starts do not fire: only
    those appearing afterwards do.
    """

    def __init__(
        self,
        coordinator: TamCoordinator,
        subentry: ConfigSubentry,
        description: EventEntityDescription,
    ) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, subentry, description)
        self._known: set[str] = set()

    async def async_added_to_hass(self) -> None:
        """Remember the alerts already in effect, which do not fire."""
        await super().async_added_to_hass()
        self._known = {alert.alert_id for alert in self._active_alerts()}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire for the alerts never seen before."""
        now = dt_util.utcnow()
        for alert in self._active_alerts():
            if alert.alert_id in self._known:
                continue
            self._known.add(alert.alert_id)
            end = alert.current_end(now)
            self._trigger_event(
                EVENT_NEW_ALERT,
                {
                    "line": self._line,
                    "title": alert.header,
                    "message": alert.description,
                    "end": end.isoformat() if end else None,
                    "url": alert.url,
                },
            )
            self.async_write_ha_state()
