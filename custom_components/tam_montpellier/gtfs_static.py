"""Parsing of the static TaM GTFS archive, reduced to the tram network.

Bus lines chosen by the user are parsed as well, as extra routes.

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
from string import ascii_lowercase
import sys
from typing import IO
import unicodedata
import zipfile
from zoneinfo import ZoneInfo

from .const import BUS_ROUTE_TYPE, TRAM_ROUTE_TYPE

type LineKey = tuple[str, int]
"""(route_id, direction_id)"""

type StopKey = tuple[str, str, int]
"""(stop_id, route_id, direction_id)"""


@dataclass(frozen=True, slots=True)
class Route:
    """A tram or bus line."""

    route_id: str
    short_name: str
    long_name: str
    color: str
    text_color: str
    route_type: str = TRAM_ROUTE_TYPE

    @property
    def is_tram(self) -> bool:
        """Return whether the line is a tram line."""
        return self.route_type == TRAM_ROUTE_TYPE

    @property
    def kind(self) -> str:
        """Return what runs on the line: "Tram" or "Bus"."""
        return "Tram" if self.is_tram else "Bus"


@dataclass(frozen=True, slots=True)
class TripInfo:
    """Static information about a trip."""

    route_id: str
    direction_id: int
    headsign: str
    service_id: str


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    """A scheduled departure of a trip from a platform of a monitored stop."""

    trip_id: str
    service_id: str
    departure: int
    """Seconds after the service day reference (may exceed 24h)."""
    headsign: str


@dataclass(slots=True)
class StaticData:
    """Tram subset of the GTFS archive, plus the bus lines chosen by the user."""

    timezone: ZoneInfo
    routes: dict[str, Route]
    """Tram lines and the chosen extra lines."""
    stop_names: dict[str, str]
    trips: dict[str, TripInfo]
    services_by_date: dict[date, frozenset[str]]
    line_stops: dict[LineKey, list[str]]
    """Ordered stop ids served by each line and direction."""
    directions: dict[LineKey, str]
    """Human readable label (main destination) of each line and direction."""
    trip_starts: dict[str, tuple[str, int]] = field(default_factory=dict)
    """trip_id -> (first stop_id, scheduled departure in seconds)."""
    trip_ends: dict[str, str] = field(default_factory=dict)
    """trip_id -> last stop_id, where the trip arrives without departing."""
    scheduled: dict[StopKey, list[ScheduledStop]] = field(default_factory=dict)
    """Scheduled departures, only for the platforms of the monitored stops."""
    platforms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Monitored stop_id -> every stop_id of the same name, itself first."""
    arrivals: dict[LineKey, frozenset[str]] = field(default_factory=dict)
    """Stops of each line direction where all trips end: none departs."""
    clockwise: dict[LineKey, bool] = field(default_factory=dict)
    """Directions of circular lines: True when running clockwise on the map."""
    variants: dict[LineKey, str] = field(default_factory=dict)
    """Letter naming a direction of a circular line, "a" for line "4a"."""
    other_routes: dict[str, Route] = field(default_factory=dict)
    """Bus lines of the archive that were not chosen, offered as extra lines."""

    def stop_platforms(self, stop_id: str) -> tuple[str, ...]:
        """Return the platforms of a stop: works may move its departures."""
        return self.platforms.get(stop_id, (stop_id,))

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

    def line_kind(self, route_id: str) -> str:
        """Return "Tram" or "Bus" for a line."""
        route = self.routes.get(route_id)
        return route.kind if route else "Tram"

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


def _significant_patterns(
    patterns: Counter[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Return the stop patterns of a line direction, its backbone first.

    The backbone is the longest (then most frequent) pattern: the whole line,
    even while works cut it into shorter runs for part of the archive.
    """
    total = patterns.total()
    return sorted(
        (
            pattern
            for pattern, count in patterns.items()
            if count >= total * _MIN_PATTERN_SHARE
        ),
        key=lambda pattern: (-len(pattern), -patterns[pattern]),
    )


def _merge_stop_orders(ordered: list[tuple[str, ...]]) -> list[str]:
    """Merge the stop patterns of a line direction into a single ordered list.

    Stops only served by other patterns than the backbone are inserted after
    their predecessor, or before their successor for the first stop of a run
    starting off the line (another platform, a depot), so that the ends of
    the backbone stay at the ends of the list.
    """
    result: list[str] = []
    for pattern in ordered:
        for index, stop_id in enumerate(pattern):
            if stop_id in result:
                continue
            if index > 0:
                result.insert(result.index(pattern[index - 1]) + 1, stop_id)
            elif len(pattern) > 1 and pattern[1] in result:
                result.insert(result.index(pattern[1]), stop_id)
            else:
                result.insert(0, stop_id)
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
    extra_routes: Iterable[str] = (),
) -> StaticData:
    """Parse a GTFS archive, keeping the tram data and the ``extra_routes`` lines.

    Scheduled departures are only extracted for the platforms of
    ``monitored_stops`` to keep the memory footprint small.
    """
    monitored = set(monitored_stops)
    extra = set(extra_routes)
    with zipfile.ZipFile(source) as archive:
        timezone = ZoneInfo("Europe/Paris")
        for row in _read_csv(archive, "agency.txt"):
            if row.get("agency_timezone"):
                timezone = ZoneInfo(row["agency_timezone"])
            break

        routes: dict[str, Route] = {}
        other_routes: dict[str, Route] = {}
        for row in _read_csv(archive, "routes.txt"):
            route_type = row.get("route_type", "")
            if route_type not in (TRAM_ROUTE_TYPE, BUS_ROUTE_TYPE):
                continue
            route = Route(
                route_id=row["route_id"],
                short_name=row.get("route_short_name") or row["route_id"],
                long_name=row.get("route_long_name", ""),
                color=(row.get("route_color") or "000000").upper(),
                text_color=(row.get("route_text_color") or "FFFFFF").upper(),
                route_type=route_type,
            )
            if route_type == TRAM_ROUTE_TYPE or route.route_id in extra:
                routes[route.route_id] = route
            else:
                other_routes[route.route_id] = route

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

        # Stops are read first: the departures of every platform named like a
        # monitored stop are extracted, as works may move them there.
        stop_rows = {row["stop_id"]: row for row in _read_csv(archive, "stops.txt")}
        all_names = {
            stop_id: row.get("stop_name", stop_id) for stop_id, row in stop_rows.items()
        }
        same_name: dict[str, list[str]] = defaultdict(list)
        for stop_id, name in sorted(all_names.items()):
            same_name[name].append(stop_id)
        platforms = {
            stop_id: (
                stop_id,
                *(p for p in same_name[all_names[stop_id]] if p != stop_id),
            )
            for stop_id in monitored
            if stop_id in all_names
        }
        extracted = monitored.union(*platforms.values())

        trip_stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
        trip_starts: dict[str, tuple[int, str, int]] = {}
        passages: list[tuple[StopKey, int, ScheduledStop]] = []
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
            if stop_id in extracted:
                passages.append(
                    (
                        (stop_id, trip.route_id, trip.direction_id),
                        sequence,
                        ScheduledStop(
                            trip_id=trip_id,
                            service_id=trip.service_id,
                            departure=parse_gtfs_time(departure),
                            headsign=trip.headsign,
                        ),
                    )
                )

        # A trip arrives at its last stop: it does not depart from it.
        trip_ends = {trip_id: max(stops) for trip_id, stops in trip_stops.items()}
        scheduled: dict[StopKey, list[ScheduledStop]] = defaultdict(list)
        for stop_key, sequence, item in passages:
            if sequence < trip_ends[item.trip_id][0]:
                scheduled[stop_key].append(item)
        del passages

        patterns: dict[LineKey, Counter[tuple[str, ...]]] = defaultdict(Counter)
        for trip_id, stops in trip_stops.items():
            trip = trips[trip_id]
            pattern = tuple(stop_id for _seq, stop_id in sorted(stops))
            patterns[(trip.route_id, trip.direction_id)][pattern] += 1
        del trip_stops

    used_stops = {stop for counter in patterns.values() for p in counter for stop in p}
    stop_names = {
        stop_id: all_names[stop_id] for stop_id in used_stops & all_names.keys()
    }
    positions: dict[str, tuple[float, float]] = {}
    for stop_id in stop_names:
        with suppress(KeyError, ValueError):
            row = stop_rows[stop_id]
            positions[stop_id] = (float(row["stop_lon"]), float(row["stop_lat"]))

    ordered = {key: _significant_patterns(p) for key, p in patterns.items()}
    line_stops = {key: _merge_stop_orders(p) for key, p in ordered.items()}
    arrivals = {
        key: frozenset(
            {pattern[-1] for pattern in p}
            - {stop for pattern in p for stop in pattern[:-1]}
        )
        for key, p in ordered.items()
    }
    for stop_list in scheduled.values():
        stop_list.sort(key=lambda item: item.departure)
    # Circular lines start and end at the same stop, on different platforms.
    # Their backbone tells them, as works may cut the loop for a while.
    backbones = {key: p[0] for key, p in ordered.items() if p}
    clockwise = {
        key: rotation
        for key, stops in backbones.items()
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
    # line "4a". The letter is then left out of the destinations. It is
    # looked for in all the headsigns: unlettered ones, or those of runs cut
    # short by works, may be the most common.
    suffixes: dict[LineKey, str] = {}
    for key in clockwise:
        terminus = stop_names.get(backbones[key][-1], "")
        letters: Counter[str] = Counter()
        for headsign, count in headsigns[key].items():
            letter = headsign.removeprefix(terminus).strip()
            if headsign.startswith(terminus) and len(letter) == 1:
                letters[letter] += count
        if letters:
            suffixes[key] = f" {letters.most_common(1)[0][0]}"
    variants = {key: suffix.strip().lower() for key, suffix in suffixes.items()}
    # A line running both ways round needs a letter for each, even unlettered.
    for key in sorted(clockwise):
        ways = [other for other in clockwise if other[0] == key[0]]
        if key not in variants and len(ways) > 1:
            used = {variants.get(way) for way in ways}
            variants[key] = next(c for c in ascii_lowercase if c not in used)
    for key in variants:
        directions[key] = stop_names.get(backbones[key][-1], "")
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
        trip_ends={trip_id: stop_id for trip_id, (_seq, stop_id) in trip_ends.items()},
        directions=directions,
        scheduled=dict(scheduled),
        platforms=platforms,
        arrivals=arrivals,
        clockwise=clockwise,
        variants=variants,
        other_routes=other_routes,
    )


# Bump when the parsing changes without changing the fields of StaticData.
_CACHE_VERSION = 3


def load_static(
    archive_path: Path,
    cache_path: Path,
    monitored_stops: Iterable[str],
    extra_routes: Iterable[str] = (),
) -> StaticData:
    """Parse the GTFS archive, reusing the result cached by a previous run.

    The cache is written next to the archive by this integration only; it is
    used again as long as the archive, the monitored stops and the extra
    lines are unchanged.
    """
    stat = archive_path.stat()
    key = (
        _CACHE_VERSION,
        tuple(f.name for f in fields(StaticData)),
        stat.st_mtime_ns,
        stat.st_size,
        tuple(sorted(set(monitored_stops))),
        tuple(sorted(set(extra_routes))),
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
    data = parse_static(archive_path, monitored_stops, extra_routes)
    try:
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps((key, data), pickle.HIGHEST_PROTOCOL))
        tmp.replace(cache_path)
    except OSError:
        pass
    return data
