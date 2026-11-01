"""Tests of the static GTFS loading helpers."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
from unittest.mock import patch
import zipfile

from custom_components.tam_montpellier import gtfs_static
from custom_components.tam_montpellier.config_flow import _direction_option
from custom_components.tam_montpellier.departures import (
    DepartureSource,
    RealtimeSnapshot,
    compute_departures,
    parse_trip_updates,
)
from custom_components.tam_montpellier.gtfs_static import (
    _read_columns,
    load_static,
    parse_static,
)

from .conftest import build_trip_updates, paris, trip_id


def test_read_columns_missing_column(tmp_path: Path) -> None:
    """A missing column reads as empty strings, blank lines are skipped."""
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("trips.txt", "﻿trip_id,route_id\n1,A\n\n2,B\n")
    with zipfile.ZipFile(path) as archive:
        rows = list(_read_columns(archive, "trips.txt", "route_id", "headsign"))
    assert rows == [("A", ""), ("B", "")]


def test_load_static_uses_cache(gtfs_zip: Path, tmp_path: Path) -> None:
    """The archive is parsed once, then read back from the cache."""
    cache = tmp_path / "static.pickle"
    parse = gtfs_static.parse_static
    with patch.object(gtfs_static, "parse_static", side_effect=parse) as mock:
        first = load_static(gtfs_zip, cache, {"B"})
        second = load_static(gtfs_zip, cache, {"B"})
    assert mock.call_count == 1
    assert second == first
    assert second.scheduled


def test_load_static_cache_invalidated(gtfs_zip: Path, tmp_path: Path) -> None:
    """Other monitored stops, a new archive or a corrupt cache parse again."""
    cache = tmp_path / "static.pickle"
    parse = gtfs_static.parse_static
    with patch.object(gtfs_static, "parse_static", side_effect=parse) as mock:
        load_static(gtfs_zip, cache, {"B"})
        load_static(gtfs_zip, cache, {"B", "C"})
        assert mock.call_count == 2

        stat = gtfs_zip.stat()
        os.utime(gtfs_zip, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        load_static(gtfs_zip, cache, {"B", "C"})
        assert mock.call_count == 3

        cache.write_bytes(b"not a pickle")
        data = load_static(gtfs_zip, cache, {"B", "C"})
        assert mock.call_count == 4
    assert data.scheduled


_LOOP_STOPS = {
    "S0": ("Sud", 43.59, 3.88),
    "W": ("Ouest - Place", 43.60, 3.87),
    "N": ("Nord", 43.61, 3.88),
    "E": ("Est", 43.60, 3.89),
    "S1": ("Sud", 43.5901, 3.8801),
}
_LOOP_TRIPS = (("a", "Sud A", 0, "S0 W N E S1", 8), ("b", "Sud B", 1, "S1 E N W S0", 8))


def _loop_gtfs(
    path: Path,
    stops: dict[str, tuple[str, float, float]] = _LOOP_STOPS,
    trips: Iterable[tuple[str, str, int, str, int]] = _LOOP_TRIPS,
) -> Path:
    """Write a circular line around a square, starting from its south corner.

    Direction 0 runs west, north, east and back south: clockwise on the map.
    Each trip (id, headsign, direction, stops, hour) leaves at the given hour
    and reaches a stop each minute.
    """
    trips = list(trips)
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "TAM,TaM,https://example.com,Europe/Paris\n",
        "routes.txt": "route_id,route_short_name,route_long_name,route_type\n"
        "4,4,Sud - Sud,0\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{i},{n},{lat},{lon}\n" for i, (n, lat, lon) in stops.items()),
        "calendar_dates.txt": "service_id,date,exception_type\nS,20260923,1\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id\n"
        + "".join(f"4,S,{trip},{headsign},{d}\n" for trip, headsign, d, *_ in trips),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(
            f"{trip},{hour:02d}:0{seq}:00,{hour:02d}:0{seq}:00,{stop},{seq}\n"
            for trip, _headsign, _d, order, hour in trips
            for seq, stop in enumerate(order.split())
        ),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_circular_line_directions(tmp_path: Path, gtfs_zip: Path) -> None:
    """Circular lines are told apart by their way round, others by destination."""
    static = parse_static(_loop_gtfs(tmp_path / "loop.zip"), {"N"})
    assert static.clockwise == {("4", 0): True, ("4", 1): False}
    assert _direction_option(static, "4", 0) == "4a · sens horaire (Ouest → Nord → Est)"
    assert (
        _direction_option(static, "4", 1)
        == "4b · sens antihoraire (Est → Nord → Ouest)"
    )
    # Each direction is named as a line, so destinations drop its letter.
    assert static.line_name("4", 1) == "4b"
    assert static.direction_label("4", 1) == "Sud"
    assert static.trips["b"].headsign == "Sud"
    assert static.scheduled[("N", "4", 1)][0].headsign == "Sud"

    static = parse_static(gtfs_zip)
    assert static.clockwise == {}
    assert static.line_name("1", 0) == "1"
    assert _direction_option(static, "1", 0) == "Vers Delta (depuis Alpha)"


def test_circular_line_cut_by_works(tmp_path: Path) -> None:
    """Works cutting a loop for a while leave it circular, each way lettered.

    Like line 4 during the 2026 rail works: shuttles from the platform of the
    other way round at Est, through Sud, to Gamma off the loop, outnumber the
    loop trips, most of which are listed without their letter.
    """
    stops = {
        **_LOOP_STOPS,
        "X": ("Xeres", 43.595, 3.888),
        "E1": ("Est", 43.6001, 3.8901),
        "G": ("Gamma", 43.60, 3.86),
    }
    trips = [
        ("a", "Sud A", 0, "S0 W N E X S1", 8),
        ("a2", "Sud", 0, "S0 W N E X S1", 10),
        ("b", "Sud B", 1, "S1 X E1 N W S0", 8),
        ("b2", "Sud", 1, "S1 X E1 N W S0", 10),
        *((f"w{i}", "Gamma", 0, "E1 X S0 W G", 12 + i) for i in range(3)),
        *((f"v{i}", "Est", 1, "G W S0 X E1", 12 + i) for i in range(3)),
    ]
    static = parse_static(_loop_gtfs(tmp_path / "works.zip", stops, trips), {"E", "S0"})
    assert static.clockwise == {("4", 0): True, ("4", 1): False}
    assert [static.line_name("4", 0), static.line_name("4", 1)] == ["4a", "4b"]
    assert static.direction_label("4", 1) == "Sud"
    # The shuttles join the loop where they meet it: its ends are kept.
    assert static.line_stops[("4", 0)] == ["S0", "W", "G", "N", "E", "E1", "X", "S1"]
    assert static.line_stops[("4", 1)] == ["S1", "X", "E1", "N", "G", "W", "S0"]
    # No tram departs from where all trips end.
    assert static.arrivals == {("4", 0): {"G", "S1"}, ("4", 1): frozenset()}

    # Est is followed on both its platforms: the shuttles leave from the
    # other one, in real time too.
    assert static.stop_platforms("E") == ("E", "E1")
    departures = compute_departures(
        static, RealtimeSnapshot(), ("E", "4", 0), paris(7, 0)
    )
    assert [(dep.time, dep.headsign) for dep in departures] == [
        (paris(8, 3), "Sud"),
        (paris(10, 3), "Sud"),
        (paris(12, 0), "Gamma"),
        (paris(13, 0), "Gamma"),
        (paris(14, 0), "Gamma"),
    ]
    payload = build_trip_updates(
        {
            "trip_id": "w0",
            "route_id": "4",
            "stops": [("E1", paris(12, 1), 60, False), ("X", paris(12, 2), 60, False)],
        }
    )
    snapshot = parse_trip_updates(payload, set(static.routes))
    departures = compute_departures(static, snapshot, ("E", "4", 0), paris(11, 30))
    assert [(dep.time, dep.source) for dep in departures] == [
        (paris(12, 1), DepartureSource.REALTIME),
        (paris(13, 0), DepartureSource.SCHEDULED),
        (paris(14, 0), DepartureSource.SCHEDULED),
    ]

    # At Sud the other way round, the loop trips leave from one platform and
    # arrive at the other, where only the shuttles depart.
    departures = compute_departures(
        static, RealtimeSnapshot(), ("S0", "4", 1), paris(7, 0)
    )
    assert [(dep.time, dep.headsign) for dep in departures] == [
        (paris(8, 0), "Sud"),
        (paris(10, 0), "Sud"),
        (paris(12, 2), "Est"),
        (paris(13, 2), "Est"),
        (paris(14, 2), "Est"),
    ]


def test_bus_lines_are_offered_not_loaded(gtfs_zip: Path) -> None:
    """Bus lines are only listed until the user picks them."""
    data = parse_static(gtfs_zip)
    assert set(data.routes) == {"1"}
    assert data.routes["1"].kind == "Tram"
    assert set(data.other_routes) == {"10"}
    assert data.other_routes["10"].kind == "Bus"
    assert not [key for key in data.line_stops if key[0] == "10"]


def test_extra_bus_line_is_loaded(gtfs_zip: Path) -> None:
    """A chosen bus line is parsed like a tram line, with its schedule."""
    data = parse_static(gtfs_zip, {"X"}, extra_routes={"10"})
    assert set(data.routes) == {"1", "10"}
    assert not data.other_routes
    assert data.line_kind("10") == "Bus"
    assert data.line_stops[("10", 0)] == ["X", "B"]
    assert [item.trip_id for item in data.scheduled[("X", "10", 0)]] == ["bus-1"]


def test_load_static_cache_depends_on_extra_routes(
    gtfs_zip: Path, tmp_path: Path
) -> None:
    """Choosing another bus line parses the archive again."""
    cache = tmp_path / "static.pickle"
    parse = gtfs_static.parse_static
    with patch.object(gtfs_static, "parse_static", side_effect=parse) as mock:
        load_static(gtfs_zip, cache, set())
        load_static(gtfs_zip, cache, set(), {"10"})
        load_static(gtfs_zip, cache, set(), {"10"})
    assert mock.call_count == 2


def test_line_colors_are_checked(gtfs_zip: Path, tmp_path: Path) -> None:
    """Only six hex digits are kept as line colours, the card writes them as is."""
    path = tmp_path / "colors.zip"
    with zipfile.ZipFile(gtfs_zip) as source, zipfile.ZipFile(path, "w") as archive:
        for name in source.namelist():
            if name != "routes.txt":
                archive.writestr(name, source.read(name))
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type,route_color,"
            "route_text_color\n"
            '1,1,Alpha - Delta,0,"red;}</style><script>",#ff00aa\n'
            "10,10,Bus line,3,005ca9 ,\n",
        )
    data = parse_static(path)
    assert (data.routes["1"].color, data.routes["1"].text_color) == ("000000", "FF00AA")
    bus = data.other_routes["10"]
    assert (bus.color, bus.text_color) == ("005CA9", "FFFFFF")


def test_previous_trips(gtfs_zip: Path) -> None:
    """Trips are chained by block, the active copies through the inactive ones."""
    static = parse_static(gtfs_zip)
    # D -> A at 08:10 (direction 1, index 1), then A -> D at 08:20 (index 2).
    assert static.previous_trips[trip_id(2)] == trip_id(1, direction=1)
    assert static.previous_trips["102"] == "201"
    # Chains stay within a service: active and inactive copies never mix.
    assert all(
        later.startswith("U_") == earlier.startswith("U_")
        for later, earlier in static.previous_trips.items()
    )
    # The first trip of a block has no predecessor.
    assert trip_id(1, direction=1) not in static.previous_trips
