"""Tests of the time to leave for the stop."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.tam_montpellier.departures import Departure, DepartureSource
from custom_components.tam_montpellier.leave import (
    is_time_to_leave,
    leave_departures,
    leave_window_changes,
)

from .conftest import PARIS, paris

WALK = timedelta(minutes=5)
WINDOW = timedelta(minutes=2)


def _departure(time: datetime) -> Departure:
    return Departure(
        time=time,
        route_id="1",
        headsign="Delta",
        source=DepartureSource.SCHEDULED,
        delay=None,
        trip_id="t",
    )


def test_leave_departures() -> None:
    """Trams that cannot be reached any more are left out."""
    departures = [_departure(paris(8, 14)), _departure(paris(8, 23))]
    leaving = leave_departures(departures, paris(8, 12), WALK)
    assert [dep.time for dep in leaving] == [paris(8, 18)]
    assert leaving[0].headsign == "Delta"


def test_leave_departures_across_dst() -> None:
    """The walk is counted in real minutes over a daylight saving change."""
    departure = _departure(datetime(2026, 3, 29, 3, 2, tzinfo=PARIS))
    now = datetime(2026, 3, 29, 1, 50, tzinfo=PARIS)
    (leaving,) = leave_departures([departure], now, WALK)
    # 03:02 summer time is 01:02 UTC; five minutes before is 00:57 UTC.
    assert (
        leaving.time.timestamp()
        == datetime(2026, 3, 29, 1, 57, tzinfo=PARIS).timestamp()
    )


@pytest.mark.parametrize(
    ("now", "on", "change"),
    [
        (paris(8, 15), False, paris(8, 16)),
        (paris(8, 16), False, paris(8, 18)),
        (paris(8, 16, 1), True, paris(8, 18)),
        (paris(8, 17, 59), True, paris(8, 18)),
        (paris(8, 18), False, None),
    ],
)
def test_time_to_leave(now: datetime, on: bool, change: datetime | None) -> None:
    """On during the window before leaving, off when it is time to go."""
    leave = paris(8, 18)
    assert is_time_to_leave(leave, now, WINDOW) is on
    assert leave_window_changes(leave, now, WINDOW) == change
