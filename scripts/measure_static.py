"""Measure the memory taken by the parsed TaM timetables.

    python scripts/measure_static.py [--gtfs GTFS.zip]

Parses the archive twice, keeping or dropping the inactive copies of the
trips, and prints the memory held by the result and its pickled size.
"""

from __future__ import annotations

import argparse
import gc
from pathlib import Path
import pickle
import tracemalloc
import urllib.request

from live_board import load_pure_modules

const, gtfs_static, _departures, _fleet = load_pure_modules()


def _held(build) -> tuple[object, int]:
    """Return what ``build`` returns, and the memory it holds afterwards."""
    gc.collect()
    tracemalloc.start()
    result = build()
    gc.collect()
    held, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, held


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gtfs", type=Path, default=Path("/tmp/tam_gtfs.zip"))
    args = parser.parse_args()
    if not args.gtfs.exists():
        urllib.request.urlretrieve(const.DEFAULT_GTFS_URL, args.gtfs)

    probe = gtfs_static.parse_static(args.gtfs)
    # The first stop of each direction of each tram line, as a user would follow.
    monitored = {stops[0] for stops in probe.line_stops.values() if stops}
    del probe

    full, full_held = _held(lambda: gtfs_static.parse_static(args.gtfs, monitored))
    full_pickle = len(pickle.dumps(full, pickle.HIGHEST_PROTOCOL))
    trips = len(full.trips)
    del full
    reduced, reduced_held = _held(
        lambda: gtfs_static._without_inactive_copies(
            gtfs_static.parse_static(args.gtfs, monitored)
        )
    )
    reduced_pickle = len(pickle.dumps(reduced, pickle.HIGHEST_PROTOCOL))

    print(f"Monitored stops: {len(monitored)}")
    print(f"Trips: {trips} -> {len(reduced.trips)}")
    print(f"Held memory: {full_held / 1e6:.2f} MB -> {reduced_held / 1e6:.2f} MB")
    print(f"Pickle: {full_pickle / 1e6:.2f} MB -> {reduced_pickle / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
