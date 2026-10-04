"""Tests of the count of trams in service."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from custom_components.tam_montpellier.departures import parse_trip_updates
from custom_components.tam_montpellier.fleet import (
    FleetTracker,
    Sighting,
    fleet_line_name,
    fleet_lines,
    parse_vehicle_positions,
    trip_update_vehicles,
)
from custom_components.tam_montpellier.gtfs_static import parse_static

from .conftest import build_trip_updates, build_vehicle_positions, paris, trip_id
from .test_gtfs_static import _loop_gtfs


def test_parse_vehicle_positions() -> None:
    """Trams are keyed by fleet number; other routes are left out on request."""
    payload = build_vehicle_positions(
        ("2004", "1", 0), ("2107", "5", None), ("240", "7", 1)
    )
    assert parse_vehicle_positions(payload, {"1", "5"}) == {
        "2004": Sighting("1", 0),
        "2107": Sighting("5", None),
    }
    assert len(parse_vehicle_positions(payload)) == 3


def test_trip_update_vehicles(gtfs_zip: Path) -> None:
    """The tram of a trip is read from the trip update, its direction from GTFS."""
    static = parse_static(gtfs_zip)
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1, direction=1),
            "direction_id": 1,
            "vehicle_id": "2030",
            "stops": [("D", paris(8, 11), None, False)],
        },
        {"trip_id": trip_id(2), "stops": [("A", paris(8, 21), None, False)]},
    )
    snapshot = parse_trip_updates(payload, set(static.routes))
    assert trip_update_vehicles(snapshot, static) == {"2030": Sighting("1", 1)}

    # Without a direction in the feed, the one of the scheduled trip is used.
    snapshot.trips[0].direction_id = None
    assert trip_update_vehicles(snapshot, static) == {"2030": Sighting("1", 1)}


def test_fleet_lines(tmp_path: Path, gtfs_zip: Path) -> None:
    """Each way of a circular line is counted apart."""
    static = parse_static(gtfs_zip)
    assert fleet_lines(static) == [("1", None)]
    assert fleet_line_name(static, ("1", None)) == "1"
    loop = parse_static(_loop_gtfs(tmp_path / "loop.zip"))
    assert fleet_lines(loop) == [("4", 0), ("4", 1)]
    assert fleet_line_name(loop, ("4", 1)) == "4b"


def test_tracker_remembers_trams_at_terminus() -> None:
    """A tram missing from the feeds stays counted for a while."""
    tracker = FleetTracker(timedelta(minutes=20))
    start = paris(13, 45).timestamp()
    tracker.update(
        {
            "2004": Sighting("1", 0),
            "2020": Sighting("1", 1),
            "2031": Sighting("4", 0),
            "2042": Sighting("4", 1),
        },
        start,
    )
    assert tracker.in_service(("1", None)) == ["2004", "2020"]
    assert tracker.in_service(("4", 0)) == ["2031"]
    assert tracker.in_service(("4", None)) == ["2031", "2042"]

    # 2004 waits at the terminus and vanishes from the feeds.
    tracker.update({"2020": Sighting("1", 0)}, start + 14 * 60)
    assert tracker.in_service(("1", None)) == ["2004", "2020"]
    assert tracker.running(("1", None)) == 1

    # It is forgotten once the memory has elapsed.
    tracker.update({"2020": Sighting("1", 0)}, start + 21 * 60)
    assert tracker.in_service(("1", None)) == ["2020"]


def test_tracker_sorts_fleet_numbers() -> None:
    """Fleet numbers are sorted as numbers."""
    tracker = FleetTracker(timedelta(minutes=20))
    tracker.update(
        {vehicle_id: Sighting("1", 0) for vehicle_id in ("2110", "998", "2004")}, 0
    )
    assert tracker.in_service(("1", None)) == ["998", "2004", "2110"]
