"""Count of the trams in service on each line.

Trams are identified by their fleet number, given by two real-time feeds:

* VehiclePosition lists the trams whose position is recent (about five
  minutes), so a tram standing at a terminus drops out of it;
* TripUpdate names the tram of a running or upcoming trip, but not always
  while it waits at a terminus.

Between two trips a tram may be missing from both feeds for a quarter of an
hour, so a tram stays counted for a while after it was last seen: the count
is steady, and a tram returning to the depot leaves it with that delay.

Trams of most lines change direction at each trip, so they are counted per
line. Those of a circular line keep going round the same way, and each
direction ("4a", "4b") is counted on its own.

This module has no Home Assistant dependency.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from google.transit import gtfs_realtime_pb2

from .departures import RealtimeSnapshot
from .gtfs_static import StaticData

type FleetLine = tuple[str, int | None]
"""(route_id, direction_id), the direction only set for circular lines."""


@dataclass(frozen=True, slots=True)
class Sighting:
    """Where a tram was seen."""

    route_id: str
    direction_id: int | None


def parse_vehicle_positions(
    payload: bytes, route_ids: set[str] | None = None
) -> dict[str, Sighting]:
    """Parse a GTFS-RT VehiclePosition feed, optionally keeping only some routes."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    vehicles: dict[str, Sighting] = {}
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        position = entity.vehicle
        trip = position.trip
        vehicle_id = position.vehicle.id or position.vehicle.label
        if not vehicle_id or not trip.route_id:
            continue
        if route_ids is not None and trip.route_id not in route_ids:
            continue
        vehicles[vehicle_id] = Sighting(
            trip.route_id, trip.direction_id if trip.HasField("direction_id") else None
        )
    return vehicles


def trip_update_vehicles(
    snapshot: RealtimeSnapshot, static: StaticData
) -> dict[str, Sighting]:
    """Return the trams assigned to the trips of a TripUpdate feed."""
    vehicles: dict[str, Sighting] = {}
    for trip in snapshot.trips:
        if trip.vehicle_id is None:
            continue
        direction_id = trip.direction_id
        if direction_id is None and (info := static.trips.get(trip.trip_id)):
            direction_id = info.direction_id
        vehicles[trip.vehicle_id] = Sighting(trip.route_id, direction_id)
    return vehicles


def fleet_lines(static: StaticData) -> list[FleetLine]:
    """Return the lines whose trams are counted, each way of a circular one apart."""
    lines: list[FleetLine] = []
    for route_id, route in static.routes.items():
        if not route.is_tram:
            continue  # Only trams are counted.
        directions = sorted(d for r, d in static.variants if r == route_id)
        lines.extend((route_id, d) for d in directions or [None])
    return lines


def fleet_line_name(static: StaticData, line: FleetLine) -> str:
    """Return the name of a counted line: "1", or "4a" for a way of line 4."""
    route_id, direction_id = line
    if direction_id is not None:
        return static.line_name(route_id, direction_id)
    route = static.routes.get(route_id)
    return route.short_name if route else route_id


class FleetTracker:
    """Remember the trams seen in the real-time feeds."""

    def __init__(self, memory: timedelta) -> None:
        """Initialize the tracker."""
        self._memory = memory.total_seconds()
        self._seen: dict[str, tuple[float, Sighting]] = {}
        self._latest: dict[str, Sighting] = {}

    def update(self, sightings: Mapping[str, Sighting], now: float) -> None:
        """Record the trams seen at a POSIX time and forget the old ones."""
        for vehicle_id, sighting in sightings.items():
            self._seen[vehicle_id] = (now, sighting)
        self._seen = {
            vehicle_id: seen
            for vehicle_id, seen in self._seen.items()
            if now - seen[0] <= self._memory
        }
        self._latest = dict(sightings)

    def in_service(self, line: FleetLine) -> list[str]:
        """Return the fleet numbers of the trams in service on a line."""
        return _sorted(
            vehicle_id
            for vehicle_id, (_seen, sighting) in self._seen.items()
            if _on_line(sighting, line)
        )

    def running(self, line: FleetLine) -> int:
        """Return the number of trams of a line in the latest feeds."""
        return sum(_on_line(sighting, line) for sighting in self._latest.values())


def _on_line(sighting: Sighting, line: FleetLine) -> bool:
    route_id, direction_id = line
    return sighting.route_id == route_id and (
        direction_id is None or sighting.direction_id == direction_id
    )


def _sorted(vehicle_ids: Iterable[str]) -> list[str]:
    """Sort fleet numbers numerically when they are numbers."""
    return sorted(vehicle_ids, key=lambda vehicle_id: (len(vehicle_id), vehicle_id))
