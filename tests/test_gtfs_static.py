"""Tests of the static GTFS loading helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import zipfile

from custom_components.tam_montpellier import gtfs_static
from custom_components.tam_montpellier.config_flow import _direction_option
from custom_components.tam_montpellier.gtfs_static import (
    _read_columns,
    load_static,
    parse_static,
)


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


def _loop_gtfs(path: Path) -> Path:
    """Write a circular line around a square, starting from its south corner.

    Direction 0 runs west, north, east and back south: clockwise on the map.
    """
    stops = {
        "S0": ("Sud", 43.59, 3.88),
        "W": ("Ouest - Place", 43.60, 3.87),
        "N": ("Nord", 43.61, 3.88),
        "E": ("Est", 43.60, 3.89),
        "S1": ("Sud", 43.5901, 3.8801),
    }
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "TAM,TaM,https://example.com,Europe/Paris\n",
        "routes.txt": "route_id,route_short_name,route_long_name,route_type\n"
        "4,4,Sud - Sud,0\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{i},{n},{lat},{lon}\n" for i, (n, lat, lon) in stops.items()),
        "calendar_dates.txt": "service_id,date,exception_type\nS,20260923,1\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id\n"
        "4,S,a,Sud A,0\n4,S,b,Sud B,1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(
            f"{trip},08:0{seq}:00,08:0{seq}:00,{stop},{seq}\n"
            for trip, order in (("a", "S0 W N E S1"), ("b", "S1 E N W S0"))
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
