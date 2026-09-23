"""Parsing of the GTFS-RT service alerts feed.

The TaM feed puts internal codes in ``header_text``
("TR/L13/Dev/13DEV 2 SENS-ROSTANDra"); the readable message is in
``description_text``.

This module has no Home Assistant dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from google.transit import gtfs_realtime_pb2

from .gtfs_static import StopKey

_LANGUAGE = "fr"


@dataclass(frozen=True, slots=True)
class InformedEntity:
    """What an alert applies to. Unset fields match anything."""

    agency_id: str | None = None
    route_id: str | None = None
    direction_id: int | None = None
    stop_id: str | None = None

    def matches(self, key: StopKey) -> bool:
        """Return whether this selector covers a stop, line and direction."""
        stop_id, route_id, direction_id = key
        if self.route_id is None and self.stop_id is None:
            # Network-wide alert (agency only) or empty selector.
            return self.agency_id is not None
        return (
            (self.route_id is None or self.route_id == route_id)
            and (self.direction_id is None or self.direction_id == direction_id)
            and (self.stop_id is None or self.stop_id == stop_id)
        )


@dataclass(frozen=True, slots=True)
class Alert:
    """A service alert."""

    alert_id: str
    header: str
    description: str
    url: str | None
    periods: tuple[tuple[datetime | None, datetime | None], ...]
    informed: tuple[InformedEntity, ...]

    def is_active(self, now: datetime) -> bool:
        """Return whether the alert is in effect (no period means always)."""
        if not self.periods:
            return True
        return any(
            (start is None or start <= now) and (end is None or now < end)
            for start, end in self.periods
        )

    def current_end(self, now: datetime) -> datetime | None:
        """Return the end of the period in effect, if any."""
        for start, end in self.periods:
            if (start is None or start <= now) and (end is None or now < end):
                return end
        return None

    def applies_to(self, key: StopKey) -> bool:
        """Return whether the alert concerns a stop, line and direction."""
        return any(entity.matches(key) for entity in self.informed)


def _text(translated: gtfs_realtime_pb2.TranslatedString) -> str:
    """Pick the French translation, or the first one."""
    texts = {t.language: t.text.strip() for t in translated.translation}
    return texts.get(_LANGUAGE) or next(iter(texts.values()), "")


def _timestamp(value: int) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


def parse_alerts(payload: bytes) -> list[Alert]:
    """Parse a GTFS-RT Alert feed."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    alerts: list[Alert] = []
    for entity in feed.entity:
        if not entity.HasField("alert") or entity.is_deleted:
            continue
        alert = entity.alert
        informed = []
        for selector in alert.informed_entity:
            route_id = selector.route_id or None
            if not route_id and selector.HasField("trip"):
                route_id = selector.trip.route_id or None
            informed.append(
                InformedEntity(
                    agency_id=selector.agency_id or None,
                    route_id=route_id,
                    direction_id=(
                        selector.direction_id
                        if selector.HasField("direction_id")
                        else None
                    ),
                    stop_id=selector.stop_id or None,
                )
            )
        header = _text(alert.header_text)
        alerts.append(
            Alert(
                alert_id=entity.id,
                header=header,
                description=_text(alert.description_text) or header,
                url=_text(alert.url) or None,
                periods=tuple(
                    (_timestamp(period.start), _timestamp(period.end))
                    for period in alert.active_period
                ),
                informed=tuple(informed),
            )
        )
    return alerts


def alerts_for(alerts: list[Alert], key: StopKey, now: datetime) -> list[Alert]:
    """Return the active alerts concerning a stop, line and direction."""
    return [alert for alert in alerts if alert.is_active(now) and alert.applies_to(key)]
