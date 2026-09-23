"""Real-time parsing and departure computation.

The TaM GTFS-RT feed has a few quirks this module deals with:

* the static GTFS contains each trip twice (``1583583687`` and
  ``U_1583583687-7``); only the prefixed copy is active and it is the one
  referenced by the real-time feed;
* trips that have not left their terminus only carry their first stop, so
  passages further down the line must be estimated from the schedule plus the
  delay announced at the terminus;
* stops are sometimes listed twice, once with times and once as ``SKIPPED``
  without times;
* a few extra runs are unknown to the static GTFS (``U_7-1-T128-1-150301-7``).

This module has no Home Assistant dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from google.transit import gtfs_realtime_pb2

from .gtfs_static import StaticData, StopKey

# Passages closer than this at the same stop are considered duplicates.
DUPLICATE_WINDOW = timedelta(seconds=60)
# Real-time data is only matched against scheduled passages around "now", as
# the same GTFS trip id runs every day of its service.
REALTIME_MATCH_WINDOW = timedelta(hours=3)
# Larger terminus delays are considered inconsistent and ignored.
MAX_TERMINUS_DELAY = timedelta(hours=1)

_StopTimeUpdate = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
_TripDescriptor = gtfs_realtime_pb2.TripDescriptor


class DepartureSource(StrEnum):
    """Where a departure time comes from."""

    REALTIME = "realtime"
    """Time announced by the real-time feed for this stop."""
    ESTIMATED = "estimated"
    """Scheduled time shifted by the delay announced at the terminus."""
    SCHEDULED = "scheduled"
    """Theoretical time, no real-time information available."""


_SOURCE_RANK = {
    DepartureSource.REALTIME: 0,
    DepartureSource.ESTIMATED: 1,
    DepartureSource.SCHEDULED: 2,
}


@dataclass(frozen=True, slots=True)
class Departure:
    """A tram passage at a stop."""

    time: datetime
    route_id: str
    headsign: str
    source: DepartureSource
    delay: int | None
    """Delay in seconds, when known."""
    trip_id: str


@dataclass(slots=True)
class RealtimeTrip:
    """A trip of the real-time feed."""

    trip_id: str
    route_id: str
    direction_id: int | None
    times: dict[str, tuple[int, int | None]] = field(default_factory=dict)
    """stop_id -> (POSIX timestamp, delay in seconds)"""
    skipped: set[str] = field(default_factory=set)
    last_stop_id: str | None = None


@dataclass(slots=True)
class RealtimeSnapshot:
    """Parsed content of a TripUpdate feed."""

    timestamp: int = 0
    trips: list[RealtimeTrip] = field(default_factory=list)
    canceled: set[str] = field(default_factory=set)


def parse_trip_updates(
    payload: bytes, route_ids: set[str] | None = None
) -> RealtimeSnapshot:
    """Parse a GTFS-RT TripUpdate feed, optionally keeping only some routes."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    snapshot = RealtimeSnapshot(timestamp=feed.header.timestamp)
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        descriptor = update.trip
        if route_ids is not None and descriptor.route_id not in route_ids:
            continue
        trip_id = descriptor.trip_id
        if descriptor.schedule_relationship == _TripDescriptor.CANCELED:
            snapshot.canceled.add(trip_id)
            continue
        trip = RealtimeTrip(
            trip_id=trip_id,
            route_id=descriptor.route_id,
            direction_id=(
                descriptor.direction_id if descriptor.HasField("direction_id") else None
            ),
        )
        last_time = 0
        for stu in update.stop_time_update:
            if stu.schedule_relationship == _StopTimeUpdate.SKIPPED:
                trip.skipped.add(stu.stop_id)
                continue
            if stu.schedule_relationship == _StopTimeUpdate.NO_DATA:
                continue
            event = stu.departure if stu.departure.time else stu.arrival
            if not event.time or stu.stop_id in trip.times:
                continue
            delay = event.delay if event.HasField("delay") else None
            trip.times[stu.stop_id] = (event.time, delay)
            if event.time >= last_time:
                last_time = event.time
                trip.last_stop_id = stu.stop_id
        # A stop listed both with times and as skipped is actually served.
        trip.skipped -= trip.times.keys()
        if trip.times or trip.skipped:
            snapshot.trips.append(trip)
    return snapshot


def compute_departures(
    static: StaticData,
    realtime: RealtimeSnapshot,
    key: StopKey,
    now: datetime,
    limit: int = 10,
) -> list[Departure]:
    """Merge real-time and scheduled data into upcoming departures at a stop."""
    stop_id, route_id, direction_id = key
    now_ts = now.timestamp()
    default_headsign = static.direction_label(route_id, direction_id)
    candidates: list[Departure] = []
    # Trips of this line direction present in the real-time feed. The value is
    # the (stop_id, timestamp, delay) announced at the terminus for trips that
    # have not started yet, None for running trips.
    realtime_trips: dict[str, tuple[str, int, int | None] | None] = {}

    for trip in realtime.trips:
        static_trip = static.trips.get(trip.trip_id)
        trip_direction = trip.direction_id
        if trip_direction is None and static_trip is not None:
            trip_direction = static_trip.direction_id
        if trip.route_id != route_id or trip_direction != direction_id:
            continue

        realtime_trips[trip.trip_id] = _terminus_update(static, trip, stop_id, now_ts)
        if stop_id not in trip.times:
            continue
        timestamp, delay = trip.times[stop_id]
        if timestamp < now_ts:
            continue
        if static_trip is not None and static_trip.headsign:
            headsign = static_trip.headsign
        elif trip.last_stop_id and trip.last_stop_id != stop_id:
            headsign = static.stop_names.get(trip.last_stop_id, default_headsign)
        else:
            headsign = default_headsign
        candidates.append(
            Departure(
                time=datetime.fromtimestamp(timestamp, static.timezone),
                route_id=route_id,
                headsign=headsign,
                source=DepartureSource.REALTIME,
                delay=delay,
                trip_id=trip.trip_id,
            )
        )

    today = now.astimezone(static.timezone).date()
    for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
        services = static.services_on(day)
        if not services:
            continue
        day_start = static.service_day_start(day)
        for scheduled in static.scheduled.get(key, ()):
            if scheduled.service_id not in services:
                continue
            when = day_start + timedelta(seconds=scheduled.departure)
            source = DepartureSource.SCHEDULED
            delay: int | None = None
            if abs(when - now) <= REALTIME_MATCH_WINDOW:
                if scheduled.trip_id in realtime.canceled:
                    continue
                if scheduled.trip_id in realtime_trips:
                    terminus = realtime_trips[scheduled.trip_id]
                    if terminus is None:
                        # Running trip: either listed above or not stopping here.
                        continue
                    delay = _terminus_delay(static, scheduled.trip_id, terminus, day)
                    if delay is not None:
                        when += timedelta(seconds=delay)
                        source = DepartureSource.ESTIMATED
            if when < now:
                continue
            candidates.append(
                Departure(
                    time=when,
                    route_id=route_id,
                    headsign=scheduled.headsign or default_headsign,
                    source=source,
                    delay=delay,
                    trip_id=scheduled.trip_id,
                )
            )

    candidates.sort(key=lambda dep: (dep.time, _SOURCE_RANK[dep.source]))
    departures: list[Departure] = []
    for candidate in candidates:
        if departures and candidate.time - departures[-1].time < DUPLICATE_WINDOW:
            if _SOURCE_RANK[candidate.source] < _SOURCE_RANK[departures[-1].source]:
                departures[-1] = candidate
            continue
        departures.append(candidate)
    return departures[:limit]


def _terminus_update(
    static: StaticData, trip: RealtimeTrip, stop_id: str, now_ts: float
) -> tuple[str, int, int | None] | None:
    """Return the terminus update of a trip that has not started yet."""
    if len(trip.times) != 1 or stop_id in trip.times or stop_id in trip.skipped:
        return None
    ((first_stop, (timestamp, delay)),) = trip.times.items()
    if timestamp < now_ts - 60:
        return None
    start = static.trip_starts.get(trip.trip_id)
    if start is not None and start[0] != first_stop:
        return None
    return first_stop, timestamp, delay


def _terminus_delay(
    static: StaticData,
    trip_id: str,
    terminus: tuple[str, int, int | None],
    day: date,
) -> int | None:
    """Return the delay of a trip at its terminus, in seconds."""
    _stop_id, timestamp, delay = terminus
    if delay is None and (start := static.trip_starts.get(trip_id)) is not None:
        scheduled = static.service_day_start(day) + timedelta(seconds=start[1])
        delay = int(timestamp - scheduled.timestamp())
    if delay is None or abs(delay) > MAX_TERMINUS_DELAY.total_seconds():
        return None
    return delay
