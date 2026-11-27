"""When to leave for the stop, given the time walked to it.

This module has no Home Assistant dependency.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .departures import Departure


def leave_departures(
    departures: list[Departure], now: datetime, walk: timedelta
) -> list[Departure]:
    """Return the departures that can still be caught, at the time to leave.

    Each departure is given at the time to leave for it: its time minus the
    walk. Computed on instants, a daylight saving change in between does
    not shift them.
    """
    walk_seconds = walk.total_seconds()
    now_ts = now.timestamp()
    leaving: list[Departure] = []
    for departure in departures:
        leave_ts = departure.time.timestamp() - walk_seconds
        if leave_ts > now_ts:
            leaving.append(
                replace(
                    departure,
                    time=datetime.fromtimestamp(leave_ts, departure.time.tzinfo),
                )
            )
    return leaving


def leave_window_changes(
    leave: datetime, now: datetime, window: timedelta
) -> datetime | None:
    """Return when "time to leave" next changes for a time to leave.

    It turns on ``window`` before ``leave``, and off at ``leave``.
    """
    on = leave.timestamp() - window.total_seconds()
    now_ts = now.timestamp()
    for change in (on, leave.timestamp()):
        if change > now_ts:
            return datetime.fromtimestamp(change, leave.tzinfo)
    return None


def is_time_to_leave(leave: datetime, now: datetime, window: timedelta) -> bool:
    """Return whether ``now`` is within ``window`` before ``leave``."""
    remaining = leave.timestamp() - now.timestamp()
    return 0 < remaining <= window.total_seconds()
