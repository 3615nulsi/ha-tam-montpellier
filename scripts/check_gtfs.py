"""Check the TaM GTFS archive for changes the integration would misread.

The operator publishes works in its daily archive a few days ahead. Run every
morning by ``.github/workflows/gtfs-watch.yml``, this script fails when a
parsing assumption no longer holds, before users notice, and lists the works
found in the archive either way:

    python scripts/check_gtfs.py [--gtfs GTFS.zip] [--no-realtime]

It exits with 1 when a check fails. The report is printed, and added to the
job summary on GitHub Actions.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.request
import zipfile

from live_board import load_pure_modules

const, gtfs_static, departures, _fleet = load_pure_modules()

# Below this share of real-time tram trips known to the archive, the trip ids
# of the two feeds no longer match: about 95 % match on a normal day.
_MIN_KNOWN_TRIPS = 0.5
# Real-time trips needed to judge it, as trams do not run at night.
_MIN_REALTIME_TRIPS = 10
# Routes running on this share of the days of the most frequent one are part
# of the usual line.
_USUAL_SHARE = 0.9


def _download(url: str) -> bytes:
    """Fetch a URL, trying again a little later when the server fails."""
    for delay in (30, 60, None):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except OSError:
            if delay is None:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def check_lines(static) -> list[str]:
    """Check what users see when adding a stop: lines, directions and stops."""
    problems = []
    trams = sorted(
        (route for route in static.routes.values() if route.is_tram),
        key=lambda route: (len(route.short_name), route.short_name),
    )
    for route in trams:
        keys = sorted(
            key
            for key, stops in static.line_stops.items()
            if key[0] == route.route_id and stops
        )
        names = [(static.line_name(*key), static.direction_label(*key)) for key in keys]
        if len(keys) != 2:
            problems.append(
                f"Line {route.short_name}: {len(keys)} directions instead of 2."
            )
        if len(set(names)) < len(names):
            problems.append(f"Line {route.short_name}: directions named alike.")
        ends = [part.strip() for part in route.long_name.split(" - ")]
        # "Garcia Lorca - Garcia Lorca": a loop run both ways round.
        circular = len(ends) == 2 and ends[0] == ends[1]
        for key, (line, label) in zip(keys, names, strict=True):
            direction = f"Line {line} towards {label}"
            if circular and key not in static.variants:
                problems.append(
                    f"{direction}: not seen as a way round of a circular line."
                )
            elif not circular and len(ends) == 2 and label not in ends:
                problems.append(
                    f"{direction}: not named after an end of {route.long_name!r}."
                )
            for stop_name, stop_id in static.offered_stops(*key).items():
                if not any(
                    static.scheduled.get((platform, *key))
                    for platform in static.stop_platforms(stop_id)
                ):
                    problems.append(
                        f"{direction}: {stop_name} is offered without any"
                        " departure in the archive."
                    )
    return problems


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    """Group sorted days into runs of consecutive days."""
    ranges: list[tuple[date, date]] = []
    for day in days:
        if ranges and day - ranges[-1][1] == timedelta(days=1):
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges


def _service_days(static) -> dict[str, set[date]]:
    """Return the days each service runs."""
    days: dict[str, set[date]] = defaultdict(set)
    for day, services in static.services_by_date.items():
        for service_id in services:
            days[service_id].add(day)
    return days


def describe_works(path: Path, static) -> list[str]:
    """List the days a tram line does not run its usual route, and what runs.

    The usual route is the one running on most days: a diversion can be
    longer, as line 3 during the October 2026 works.
    """
    service_days = _service_days(static)
    trams = {route_id for route_id, route in static.routes.items() if route.is_tram}
    trip_stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        for trip_id, stop_id, sequence in gtfs_static._read_columns(
            archive, "stop_times.txt", "trip_id", "stop_id", "stop_sequence"
        ):
            trip = static.trips.get(trip_id)
            if trip is not None and trip.route_id in trams:
                trip_stops[trip_id].append((int(sequence), stop_id))

    patterns: dict[tuple[str, int], Counter[tuple[str, ...]]] = defaultdict(Counter)
    pattern_days: dict[tuple[str, ...], set[date]] = defaultdict(set)
    for trip_id, stops in trip_stops.items():
        trip = static.trips[trip_id]
        pattern = tuple(stop_id for _seq, stop_id in sorted(stops))
        patterns[(trip.route_id, trip.direction_id)][pattern] += 1
        pattern_days[pattern] |= service_days[trip.service_id]
    tram_days = set().union(*pattern_days.values())

    def names(stop_ids) -> list[str]:
        """Return the names of stops, each once."""
        return list(dict.fromkeys(static.stop_names.get(s, s) for s in stop_ids))

    works = []
    for key in sorted(patterns):
        ordered = gtfs_static._significant_patterns(patterns[key])
        if not ordered:
            continue
        usual = max(ordered, key=lambda p: (len(pattern_days[p]), patterns[key][p]))
        # Branches alternate, as on line 3: all routes running about as often
        # make up the usual line.
        most = len(pattern_days[usual])
        usual_stops = names(
            stop_id
            for pattern in ordered
            if len(pattern_days[pattern]) >= _USUAL_SHARE * most
            for stop_id in pattern
        )
        line = f"Line {static.line_name(*key)} towards {static.direction_label(*key)}"
        for first, last in _ranges(sorted(tram_days - pattern_days[usual])):
            period = {first + timedelta(days=n) for n in range((last - first).days + 1)}
            running = [p for p in ordered if pattern_days[p] & period]
            served = names(stop_id for pattern in running for stop_id in pattern)
            routes = ", ".join(" → ".join(names((p[0], p[-1]))) for p in running)
            text = f"{line}, {first} to {last}: runs {routes or 'nothing'}"
            if skipped := [stop for stop in usual_stops if stop not in served]:
                text += f"; not served: {', '.join(skipped)}"
            if added := [stop for stop in served if stop not in usual_stops]:
                text += f"; new stops: {', '.join(added)}"
            works.append(text + ".")
    return works


def check_realtime(static) -> tuple[list[str], str]:
    """Check that the real-time tram trips are found in the archive."""
    try:
        payload = _download(const.DEFAULT_TRIP_UPDATES_URL)
    except OSError as err:
        return [], f"Real-time feed unavailable: {err}"
    trams = {route_id for route_id, route in static.routes.items() if route.is_tram}
    trips = departures.parse_trip_updates(payload, trams).trips
    known = sum(trip.trip_id in static.trips for trip in trips)
    note = f"Real-time feed: {known} of {len(trips)} tram trips known to the archive."
    if len(trips) >= _MIN_REALTIME_TRIPS and known < len(trips) * _MIN_KNOWN_TRIPS:
        return [f"{note} The trip ids of the two feeds no longer match."], note
    return [], note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gtfs", type=Path, help="local GTFS.zip (else downloaded)")
    parser.add_argument(
        "--no-realtime", action="store_true", help="skip the real-time feed check"
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        path = args.gtfs
        if path is None:
            path = Path(tmp) / "GTFS.zip"
            path.write_bytes(_download(const.DEFAULT_GTFS_URL))
        catalog = gtfs_static.parse_static(path)
        stops = {
            stop_id
            for (route_id, _direction), stop_ids in catalog.line_stops.items()
            if catalog.routes[route_id].is_tram
            for stop_id in stop_ids
        }
        static = gtfs_static.parse_static(path, stops)
        problems = check_lines(static)
        works = describe_works(path, static)
    note = "Real-time feed not checked."
    if not args.no_realtime:
        realtime_problems, note = check_realtime(static)
        problems += realtime_problems

    tram_services = {
        trip.service_id
        for trip in static.trips.values()
        if static.routes[trip.route_id].is_tram
    }
    service_days = _service_days(static)
    days = sorted(set().union(*(service_days[s] for s in tram_services)))
    report = [
        "## TaM GTFS check",
        "",
        f"Archive from {days[0]} to {days[-1]}." if days else "Empty archive.",
        "",
        "### Problems",
        "",
        *([f"- {problem}" for problem in problems] or ["None."]),
        "",
        "### Works",
        "",
        *([f"- {item}" for item in works] or ["None."]),
        "",
        note,
    ]
    text = "\n".join(report)
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
