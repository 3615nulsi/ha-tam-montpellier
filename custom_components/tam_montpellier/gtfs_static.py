"""Parsing of the static TaM GTFS archive, reduced to the tram network.

This module has no Home Assistant dependency so it can be unit tested and
run from scripts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import suppress
import csv
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta
import io
from operator import itemgetter
from pathlib import Path
import pickle
import re
import sys
from typing import IO
import unicodedata
import zipfile
from zoneinfo import ZoneInfo

from .const import TRAM_ROUTE_TYPE

type LineKey = tuple[str, int]
"""(route_id, direction_id)"""

type StopKey = tuple[str, str, int]
"""(stop_id, route_id, direction_id)"""


@dataclass(frozen=True, slots=True)
class Route:
    """A tram line."""

    route_id: str
    short_name: str
    long_name: str
    color: str
    text_color: str


@dataclass(frozen=True, slots=True)
class TripInfo:
    """Static information about a trip."""

    route_id: str
    direction_id: int
    headsign: str
    service_id: str


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    """A scheduled passage of a trip at a monitored stop."""

    trip_id: str
    service_id: str
    departure: int
    """Seconds after the service day reference (may exceed 24h)."""
    headsign: str


@dataclass(slots=True)
class StaticData:
    """Tram subset of the GTFS archive."""

    timezone: ZoneInfo
    routes: dict[str, Route]
    stop_names: dict[str, str]
    trips: dict[str, TripInfo]
    services_by_date: dict[date, frozenset[str]]
    line_stops: dict[LineKey, list[str]]
    """Ordered stop ids served by each line and direction."""
    directions: dict[LineKey, str]
    """Human readable label (main destination) of each line and direction."""
    trip_starts: dict[str, tuple[str, int]] = field(default_factory=dict)
    """trip_id -> (first stop_id, scheduled departure in seconds)."""
    scheduled: dict[StopKey, list[ScheduledStop]] = field(default_factory=dict)
    """Scheduled passages, only for the monitored stops."""
    clockwise: dict[LineKey, bool] = field(default_factory=dict)
    """Directions of circular lines: True when running clockwise on the map."""
    variants: dict[LineKey, str] = field(default_factory=dict)
    """Letter naming a direction of a circular line, "a" for line "4a"."""

    def services_on(self, day: date) -> frozenset[str]:
        """Return the service ids running on a given day."""
        return self.services_by_date.get(day, frozenset())

    def service_day_start(self, day: date) -> datetime:
        """Return the GTFS reference time of a service day ("noon minus 12h")."""
        return datetime.combine(day, time(12), self.timezone) - timedelta(hours=12)

    def line_name(self, route_id: str, direction_id: int) -> str:
        """Return the name of a line, each direction of a circular one apart."""
        route = self.routes.get(route_id)
        name = route.short_name if route else route_id
        return name + self.variants.get((route_id, direction_id), "")

    def direction_label(self, route_id: str, direction_id: int) -> str:
        """Return the label of a direction, falling back to its terminus."""
        if label := self.directions.get((route_id, direction_id)):
            return label
        if stops := self.line_stops.get((route_id, direction_id)):
            return self.stop_names.get(stops[-1], stops[-1])
        return f"Direction {direction_id}"


def parse_gtfs_time(value: str) -> int:
    """Parse a GTFS HH:MM:SS time (hours may exceed 23) into seconds."""
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _read_csv(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def _read_columns(
    archive: zipfile.ZipFile, name: str, *columns: str
) -> Iterator[tuple[str, ...]]:
    """Read some columns of a large file, empty when a column is missing.

    About twice as fast as ``csv.DictReader`` on the million rows of
    ``stop_times.txt``.
    """
    with archive.open(name) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        header = {column: i for i, column in enumerate(next(reader, []))}
        indexes = [header.get(column) for column in columns]
        if None not in indexes:
            getter = itemgetter(*indexes)
            yield from (getter(row) for row in reader if row)
            return
        for row in reader:
            if row:
                yield tuple(
                    row[i] if i is not None and i < len(row) else "" for i in indexes
                )


# Stop patterns used by less than this share of the trips of a line direction
# (depot runs, diversions) are ignored when listing the stops of a line.
_MIN_PATTERN_SHARE = 0.01


def _merge_stop_orders(patterns: Counter[tuple[str, ...]]) -> list[str]:
    """Merge the stop patterns of a line direction into a single ordered list.

    The longest (then most frequent) pattern is the backbone; stops only served
    by other patterns are inserted after their predecessor.
    """
    total = patterns.total()
    ordered = sorted(
        (
            (pattern, count)
            for pattern, count in patterns.items()
            if count >= total * _MIN_PATTERN_SHARE
        ),
        key=lambda item: (-len(item[0]), -item[1]),
    )
    result: list[str] = []
    for pattern, _count in ordered:
        for index, stop_id in enumerate(pattern):
            if stop_id in result:
                continue
            if index == 0:
                result.insert(0, stop_id)
            else:
                result.insert(result.index(pattern[index - 1]) + 1, stop_id)
    return result


def _name_tokens(name: str) -> set[str]:
    """Return the significant words of a stop or line name, without accents."""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    )
    return {word for word in re.split(r"[^a-z0-9]+", ascii_name) if len(word) > 2}


def _direction_label(
    route: Route,
    stops: list[str],
    stop_names: dict[str, str],
    headsigns: Counter[str],
) -> str:
    """Label a direction with its end of the official line name.

    Line names read "Mosson - Gare Sud de France"; the half sharing words with
    the terminus of the direction is used. It covers branches ("Lattes /
    Pérols") better than trip headsigns, which often name short workings.
    Circular lines fall back to the most common headsign.
    """
    ends = [part.strip() for part in route.long_name.split(" - ")]
    if len(ends) == 2 and ends[0] != ends[1] and stops:
        terminus = _name_tokens(stop_names.get(stops[-1], ""))
        overlaps = [len(_name_tokens(end) & terminus) for end in ends]
        if overlaps[0] != overlaps[1]:
            return ends[overlaps.index(max(overlaps))]
    if headsigns:
        return headsigns.most_common(1)[0][0]
    return stop_names.get(stops[-1], stops[-1]) if stops else route.short_name


def _is_clockwise(
    stops: list[str], positions: dict[str, tuple[float, float]]
) -> bool | None:
    """Tell if a loop runs clockwise on the map, from its signed area."""
    points = [positions[stop_id] for stop_id in stops if stop_id in positions]
    area = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True)
    )
    return area < 0 if len(points) > 2 and area else None


def parse_static(
    source: str | Path | IO[bytes],
    monitored_stops: Iterable[str] = (),
) -> StaticData:
    """Parse a GTFS archive, keeping only tram data.

    Scheduled passages are only extracted for ``monitored_stops`` to keep the
    memory footprint small.
    """
    monitored = set(monitored_stops)
    with zipfile.ZipFile(source) as archive:
        timezone = ZoneInfo("Europe/Paris")
        for row in _read_csv(archive, "agency.txt"):
            if row.get("agency_timezone"):
                timezone = ZoneInfo(row["agency_timezone"])
            break

        routes: dict[str, Route] = {}
        for row in _read_csv(archive, "routes.txt"):
            if row.get("route_type") != TRAM_ROUTE_TYPE:
                continue
            routes[row["route_id"]] = Route(
                route_id=row["route_id"],
                short_name=row.get("route_short_name") or row["route_id"],
                long_name=row.get("route_long_name", ""),
                color=(row.get("route_color") or "000000").upper(),
                text_color=(row.get("route_text_color") or "FFFFFF").upper(),
            )

        trips: dict[str, TripInfo] = {}
        headsigns: dict[LineKey, Counter[str]] = defaultdict(Counter)
        for trip_id, route_id, direction_id, headsign, service_id in _read_columns(
            archive,
            "trips.txt",
            "trip_id",
            "route_id",
            "direction_id",
            "trip_headsign",
            "service_id",
        ):
            if route_id not in routes:
                continue
            info = TripInfo(
                route_id=route_id,
                direction_id=int(direction_id or 0),
                headsign=headsign.strip(),
                service_id=sys.intern(service_id),
            )
            trips[trip_id] = info
            if info.headsign:
                headsigns[(info.route_id, info.direction_id)][info.headsign] += 1

        services: dict[str, set[str]] = defaultdict(set)
        names = set(archive.namelist())
        if "calendar.txt" in names:
            weekdays = (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
            for row in _read_csv(archive, "calendar.txt"):
                day = datetime.strptime(row["start_date"], "%Y%m%d").date()
                end = datetime.strptime(row["end_date"], "%Y%m%d").date()
                while day <= end:
                    if row[weekdays[day.weekday()]] == "1":
                        services[day.isoformat()].add(row["service_id"])
                    day += timedelta(days=1)
        if "calendar_dates.txt" in names:
            for row in _read_csv(archive, "calendar_dates.txt"):
                day_key = datetime.strptime(row["date"], "%Y%m%d").date().isoformat()
                if row["exception_type"] == "1":
                    services[day_key].add(row["service_id"])
                else:
                    services[day_key].discard(row["service_id"])
        services_by_date = {
            date.fromisoformat(day): frozenset(ids) for day, ids in services.items()
        }

        trip_stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
        trip_starts: dict[str, tuple[int, str, int]] = {}
        scheduled: dict[StopKey, list[ScheduledStop]] = defaultdict(list)
        for trip_id, raw_stop_id, raw_sequence, departure, arrival in _read_columns(
            archive,
            "stop_times.txt",
            "trip_id",
            "stop_id",
            "stop_sequence",
            "departure_time",
            "arrival_time",
        ):
            trip = trips.get(trip_id)
            if trip is None:
                continue
            stop_id = sys.intern(raw_stop_id)
            sequence = int(raw_sequence)
            trip_stops[trip_id].append((sequence, stop_id))
            departure = departure or arrival
            if not departure:
                continue
            start = trip_starts.get(trip_id)
            if start is None or sequence < start[0]:
                trip_starts[trip_id] = (
                    sequence,
                    stop_id,
                    parse_gtfs_time(departure),
                )
            if stop_id in monitored:
                scheduled[(stop_id, trip.route_id, trip.direction_id)].append(
                    ScheduledStop(
                        trip_id=trip_id,
                        service_id=trip.service_id,
                        departure=parse_gtfs_time(departure),
                        headsign=trip.headsign,
                    )
                )

        patterns: dict[LineKey, Counter[tuple[str, ...]]] = defaultdict(Counter)
        for trip_id, stops in trip_stops.items():
            trip = trips[trip_id]
            pattern = tuple(stop_id for _seq, stop_id in sorted(stops))
            patterns[(trip.route_id, trip.direction_id)][pattern] += 1
        del trip_stops

        used_stops = {
            stop for counter in patterns.values() for p in counter for stop in p
        }
        stop_names: dict[str, str] = {}
        positions: dict[str, tuple[float, float]] = {}
        for row in _read_csv(archive, "stops.txt"):
            if row["stop_id"] in used_stops:
                stop_names[row["stop_id"]] = row.get("stop_name", row["stop_id"])
                with suppress(KeyError, ValueError):
                    positions[row["stop_id"]] = (
                        float(row["stop_lon"]),
                        float(row["stop_lat"]),
                    )

    line_stops = {key: _merge_stop_orders(p) for key, p in patterns.items()}
    for stop_list in scheduled.values():
        stop_list.sort(key=lambda item: item.departure)
    # Circular lines start and end at the same stop, on different platforms.
    clockwise = {
        key: rotation
        for key, stops in line_stops.items()
        if len(stops) > 2
        and stop_names.get(stops[0]) == stop_names.get(stops[-1])
        and (rotation := _is_clockwise(stops, positions)) is not None
    }

    directions = {
        key: _direction_label(routes[key[0]], stops, stop_names, headsigns[key])
        for key, stops in line_stops.items()
    }

    # Each direction of a circular line is presented as a line of its own,
    # named after the letter ending its headsigns: "Garcia Lorca A" runs on
    # line "4a". The letter is then left out of the destinations.
    suffixes: dict[LineKey, str] = {}
    for key in clockwise:
        terminus = stop_names.get(line_stops[key][-1], "")
        letter = directions[key].removeprefix(terminus).strip()
        if directions[key].startswith(terminus) and len(letter) == 1:
            suffixes[key] = f" {letter}"
            directions[key] = terminus
    if suffixes:
        for trip_id, trip in trips.items():
            if suffix := suffixes.get((trip.route_id, trip.direction_id)):
                trips[trip_id] = replace(
                    trip, headsign=trip.headsign.removesuffix(suffix)
                )
        for stop_key, stop_list in scheduled.items():
            if suffix := suffixes.get(stop_key[1:]):
                stop_list[:] = [
                    replace(item, headsign=item.headsign.removesuffix(suffix))
                    for item in stop_list
                ]

    return StaticData(
        timezone=timezone,
        routes=routes,
        stop_names=stop_names,
        trips=trips,
        services_by_date=services_by_date,
        line_stops=line_stops,
        trip_starts={
            trip_id: (stop_id, departure)
            for trip_id, (_seq, stop_id, departure) in trip_starts.items()
        },
        directions=directions,
        scheduled=dict(scheduled),
        clockwise=clockwise,
        variants={key: suffix.strip().lower() for key, suffix in suffixes.items()},
    )


# Bump when the parsing changes without changing the fields of StaticData.
_CACHE_VERSION = 1


def load_static(
    archive_path: Path, cache_path: Path, monitored_stops: Iterable[str]
) -> StaticData:
    """Parse the GTFS archive, reusing the result cached by a previous run.

    The cache is written next to the archive by this integration only; it is
    used again as long as the archive and the monitored stops are unchanged.
    """
    stat = archive_path.stat()
    key = (
        _CACHE_VERSION,
        tuple(f.name for f in fields(StaticData)),
        stat.st_mtime_ns,
        stat.st_size,
        tuple(sorted(set(monitored_stops))),
    )
    try:
        with cache_path.open("rb") as file:
            cached_key, data = pickle.load(file)
        if cached_key == key and isinstance(data, StaticData):
            return data
    except (
        OSError,
        EOFError,
        pickle.UnpicklingError,
        AttributeError,
        ImportError,
        IndexError,
        TypeError,
        ValueError,
    ):
        pass  # Missing, outdated or unreadable: parse the archive again.
    data = parse_static(archive_path, monitored_stops)
    try:
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps((key, data), pickle.HIGHEST_PROTOCOL))
        tmp.replace(cache_path)
    except OSError:
        pass
    return data
