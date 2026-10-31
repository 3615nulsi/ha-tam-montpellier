"""Tests of the GTFS parsing and departure computation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from custom_components.tam_montpellier.departures import (
    Departure,
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
from custom_components.tam_montpellier.sensor import (
    minutes_until,
    next_minute_change,
)

from .conftest import PARIS, build_trip_updates, paris, trip_id

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


def test_added_runs_are_hidden_by_default(static: StaticData) -> None:
    """Runs flagged ADDED are left out, as in the official app, unless asked for.

    During the disruption of 31 October 2026, TaM kept announcing the regular
    trips and added replacement runs next to them, which M'Ticket ignored.
    """
    payload = build_trip_updates(
        {
            "trip_id": "U_10-2-T216-0-160640",
            "added": True,
            "stops": [
                ("B", paris(8, 18), 0, False),
                ("C", paris(8, 21), 0, False),
            ],
        }
    )
    snapshot = parse_trip_updates(payload, set(static.routes))
    assert snapshot.trips[0].added

    hidden = compute_departures(static, snapshot, KEY_B, paris(8, 15))
    assert all(dep.trip_id != "U_10-2-T216-0-160640" for dep in hidden)
    assert hidden == _departures(static, None, paris(8, 15))

    shown = compute_departures(
        static, snapshot, KEY_B, paris(8, 15), include_added=True
    )
    added = next(dep for dep in shown if dep.trip_id == "U_10-2-T216-0-160640")
    assert added.time == paris(8, 18)
    assert added.headsign == "Charlie"


def test_arrival_at_terminus_is_not_a_departure(gtfs_zip: Path) -> None:
    """Trams reaching the end of their trip do not depart from it."""
    static = parse_static(gtfs_zip, {"D"})
    assert ("D", "1", 0) not in static.scheduled
    assert static.scheduled[("D", "1", 1)]
    payload = build_trip_updates(
        {
            "trip_id": trip_id(1),
            "stops": [("C", paris(8, 16), 0, False), ("D", paris(8, 19), 0, False)],
        },
        {
            "trip_id": "U_7-1-T128-1-150301-7",
            "stops": [("C", paris(8, 26), 0, False), ("D", paris(8, 29), 0, False)],
        },
    )
    assert _departures(static, payload, paris(8, 15), ("D", "1", 0)) == []


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


@pytest.mark.parametrize(
    ("now", "minutes", "change"),
    [
        (paris(8, 12, 0), 2, paris(8, 12, 30)),
        (paris(8, 12, 30), 2, paris(8, 12, 30)),
        (paris(8, 12, 31), 1, paris(8, 13, 30)),
        (paris(8, 14, 0), 0, paris(8, 14, 30)),
    ],
)
def test_minutes_rounded_down(now, minutes: int, change) -> None:
    """Minutes are rounded down and the next change is on the boundary."""
    departure = Departure(
        time=paris(8, 14, 30),
        route_id="1",
        headsign="Delta",
        source=DepartureSource.REALTIME,
        delay=None,
        trip_id="t",
    )
    assert minutes_until(departure, now) == minutes
    assert next_minute_change(departure, now) - change < timedelta(seconds=1)
    assert next_minute_change(departure, now) > now


@pytest.mark.parametrize(
    ("departure_time", "now"),
    [
        # Autumn: the first tram of the next morning is in winter time.
        (
            datetime(2026, 10, 25, 7, 5, tzinfo=PARIS),
            datetime(2026, 10, 24, 23, 38, tzinfo=PARIS),
        ),
        # Spring: the first tram of the next morning is in summer time.
        (
            datetime(2027, 3, 28, 5, 5, tzinfo=PARIS),
            datetime(2027, 3, 27, 23, 38, tzinfo=PARIS),
        ),
    ],
)
def test_next_minute_change_across_dst(departure_time, now) -> None:
    """A daylight saving change before the departure keeps the refresh ahead.

    Subtracting minutes from the local wall clock was an hour off, scheduling
    the refresh in the past: it fired again at once, pegging the event loop.
    """
    departure = Departure(
        time=departure_time,
        route_id="1",
        headsign="Delta",
        source=DepartureSource.SCHEDULED,
        delay=None,
        trip_id="t",
    )
    change = next_minute_change(departure, now)
    assert change > now
    assert change - now <= timedelta(minutes=1, seconds=1)
    # At the change, the displayed count has just dropped by one.
    assert minutes_until(departure, change) == minutes_until(departure, now) - 1
