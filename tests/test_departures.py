"""Tests of the GTFS parsing and departure computation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from custom_components.tam_montpellier.departures import (
    DepartureSource,
    RealtimeSnapshot,
    compute_departures,
    parse_trip_updates,
)
from custom_components.tam_montpellier.gtfs_static import (
    Route,
    StaticData,
    _direction_label,
    parse_static,
)

from .conftest import build_trip_updates, paris, trip_id

KEY_B = ("B", "1", 0)


@pytest.fixture(name="static")
def static_fixture(gtfs_zip: Path) -> StaticData:
    """Return the parsed synthetic GTFS, monitoring stop B."""
    return parse_static(gtfs_zip, {"B"})


def _departures(static: StaticData, payload: bytes | None, now, key=KEY_B):
    snapshot = (
        parse_trip_updates(payload, set(static.routes))
        if payload is not None
        else RealtimeSnapshot()
    )
    return compute_departures(static, snapshot, key, now)


def test_parse_static(static: StaticData) -> None:
    """Only tram data is kept and stops are ordered along the line."""
    assert set(static.routes) == {"1"}
    assert static.routes["1"].color == "005CA9"
    assert static.line_stops[("1", 0)] == ["A", "B", "C", "D"]
    assert static.line_stops[("1", 1)] == ["D", "C", "B", "A"]
    assert static.direction_label("1", 0) == "Delta"
    assert static.stop_names["B"] == "Bravo"
    assert "bus-1" not in static.trips
    assert static.trip_starts[trip_id(0)] == ("A", 8 * 3600)
    # Only the monitored stop is extracted, for the tram only.
    assert set(static.scheduled) == {("B", "1", 0), ("B", "1", 1)}


@pytest.mark.parametrize(
    ("long_name", "terminus", "expected"),
    [
        # Branches: the line name is better than the most common headsign.
        ("Juvignac - Lattes / Pérols", "Pérols Étang de l'Or", "Lattes / Pérols"),
        (
            "St-Jean de Védas Centre - Jacou",
            "Saint-Jean de Védas Centre",
            "St-Jean de Védas Centre",
        ),
        # Circular line: both ends are identical, the headsign is used.
        ("Garcia Lorca - Garcia Lorca", "Garcia Lorca", "Garcia Lorca A"),
    ],
)
def test_direction_label(long_name: str, terminus: str, expected: str) -> None:
    """Directions are labelled with the matching end of the line name."""
    route = Route("3", "3", long_name, "000000", "FFFFFF")
    headsigns = Counter({"Garcia Lorca A": 9, "Gambetta": 2})
    label = _direction_label(route, ["s1", "s2"], {"s2": terminus}, headsigns)
    assert label == expected


def test_scheduled_only(static: StaticData) -> None:
    """Without real-time data, scheduled passages of active trips are used."""
    departures = _departures(static, None, paris(8, 5))
    assert [dep.time for dep in departures[:3]] == [
        paris(8, 13),
        paris(8, 23),
        paris(8, 33),
    ]
    assert all(dep.source is DepartureSource.SCHEDULED for dep in departures)
    assert departures[0].headsign == "Delta"
    assert departures[0].trip_id == trip_id(1)


def test_realtime_running_trip(static: StaticData) -> None:
    """A running trip uses the time announced at the stop."""
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1),
            "stops": [
                ("A", paris(8, 11), 60, False),
                ("B", paris(8, 14, 30), 90, False),
                ("C", paris(8, 17, 30), 90, False),
            ],
        }
    )
    departures = _departures(static, payload, paris(8, 12))
    first = departures[0]
    assert first.time == paris(8, 14, 30)
    assert first.source is DepartureSource.REALTIME
    assert first.delay == 90
    # The scheduled passage of the same trip is not listed twice.
    assert [dep.trip_id for dep in departures].count(trip_id(1)) == 1


def test_not_started_trip_is_estimated(static: StaticData) -> None:
    """A trip announced at its terminus only is shifted by the terminus delay.

    The TaM feed gives no delay for these updates: it is computed from the
    scheduled departure at the terminus.
    """
    payload = build_trip_updates(
        {"trip_id": trip_id(2), "stops": [("A", paris(8, 22), None, False)]}
    )
    departures = _departures(static, payload, paris(8, 15))
    first = departures[0]
    assert first.trip_id == trip_id(2)
    assert first.time == paris(8, 25)
    assert first.source is DepartureSource.ESTIMATED
    assert first.delay == 120


def test_canceled_trip(static: StaticData) -> None:
    """Canceled trips are removed."""
    payload = build_trip_updates({"trip_id": trip_id(1), "canceled": True})
    departures = _departures(static, payload, paris(8, 5))
    assert trip_id(1) not in [dep.trip_id for dep in departures]
    assert departures[0].time == paris(8, 23)


def test_skipped_stop(static: StaticData) -> None:
    """A running trip skipping the stop is not listed."""
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1),
            "stops": [
                ("A", paris(8, 10), 0, False),
                ("B", None, None, True),
                ("C", paris(8, 16), 0, False),
            ],
        }
    )
    departures = _departures(static, payload, paris(8, 11))
    assert trip_id(1) not in [dep.trip_id for dep in departures]


def test_duplicated_skipped_entry_is_served(static: StaticData) -> None:
    """The feed lists stops twice, timed then SKIPPED: the stop is served."""
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1),
            "stops": [
                ("A", paris(8, 10), 0, False),
                ("A", None, None, True),
                ("B", paris(8, 13, 20), 20, False),
                ("B", None, None, True),
            ],
        }
    )
    departures = _departures(static, payload, paris(8, 11))
    assert departures[0].trip_id == trip_id(1)
    assert departures[0].source is DepartureSource.REALTIME


def test_unknown_trip_uses_last_stop_as_destination(static: StaticData) -> None:
    """Extra runs unknown to the GTFS are shown, headed to their last stop."""
    payload = build_trip_updates(
        {
            "trip_id": "U_7-1-T128-1-150301-7",
            "stops": [
                ("B", paris(8, 18), 0, False),
                ("C", paris(8, 21), 0, False),
            ],
        }
    )
    departures = _departures(static, payload, paris(8, 15))
    extra = next(dep for dep in departures if dep.trip_id.startswith("U_7-1-T128"))
    assert extra.headsign == "Charlie"
    assert extra.time == paris(8, 18)


def test_duplicates_are_merged(static: StaticData) -> None:
    """Passages less than a minute apart are merged, real time first."""
    payload = build_trip_updates(
        {
            "trip_id": "U_ghost-7",
            "stops": [("B", paris(8, 23, 30), 0, False), ("C", paris(8, 26), 0, False)],
        }
    )
    departures = _departures(static, payload, paris(8, 15))
    assert departures[0].time == paris(8, 23, 30)
    assert departures[0].source is DepartureSource.REALTIME
    assert departures[1].time == paris(8, 33)


def test_after_midnight(static: StaticData) -> None:
    """Trips after midnight belong to the previous service day."""
    departures = _departures(static, None, paris(0, 10, day=24))
    assert departures[0].time == paris(0, 33, day=24)


def test_other_direction_and_line_ignored(static: StaticData) -> None:
    """Real-time trips of other directions do not leak into the stop."""
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1, direction=1),
            "direction_id": 1,
            "stops": [("B", paris(8, 16), 0, False)],
        },
        {
            "trip_id": "bus-1",
            "route_id": "10",
            "stops": [("B", paris(8, 17), 0, False)],
        },
    )
    departures = _departures(static, payload, paris(8, 15))
    assert departures[0].time == paris(8, 23)
