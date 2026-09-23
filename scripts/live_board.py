"""Print a live departure board from the real TaM feeds.

Development helper to check the parsing logic against production data:

    python scripts/live_board.py "Comédie" --line 1
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import time
import types
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "custom_components" / "tam_montpellier"


def _load_pure_modules() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    """Load the HA-independent modules without importing the integration package."""
    package = types.ModuleType("tam_montpellier")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["tam_montpellier"] = package
    modules = []
    for name in ("const", "gtfs_static", "departures"):
        spec = importlib.util.spec_from_file_location(
            f"tam_montpellier.{name}", PACKAGE_DIR / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return tuple(modules)


def main() -> None:
    const, gtfs_static, departures = _load_pure_modules()
    parser = argparse.ArgumentParser()
    parser.add_argument("stop", help="stop name (substring match)")
    parser.add_argument("--line", help="tram line (route_short_name)")
    parser.add_argument(
        "--gtfs", type=Path, help="local GTFS.zip (downloaded if absent)"
    )
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    gtfs_path = args.gtfs
    if gtfs_path is None or not gtfs_path.exists():
        gtfs_path = gtfs_path or Path("/tmp/tam_gtfs.zip")
        print(f"Downloading GTFS to {gtfs_path}…")
        urllib.request.urlretrieve(const.DEFAULT_GTFS_URL, gtfs_path)

    started = time.monotonic()
    catalog = gtfs_static.parse_static(gtfs_path)
    keys = [
        (stop_id, route_id, direction_id)
        for (route_id, direction_id), stops in catalog.line_stops.items()
        if args.line is None or catalog.routes[route_id].short_name == args.line
        for stop_id in stops
        if args.stop.lower() in catalog.stop_names[stop_id].lower()
    ]
    static = gtfs_static.parse_static(gtfs_path, {key[0] for key in keys})
    print(f"Static data parsed twice in {time.monotonic() - started:.1f}s")

    with urllib.request.urlopen(const.DEFAULT_TRIP_UPDATES_URL, timeout=20) as resp:
        payload = resp.read()
    snapshot = departures.parse_trip_updates(payload, set(static.routes))
    now = datetime.now(static.timezone)
    print(f"Real-time feed: {len(snapshot.trips)} tram trips, now {now:%H:%M:%S}\n")

    for key in sorted(keys, key=lambda k: (k[1], k[2])):
        stop_id, route_id, direction_id = key
        route = static.routes[route_id]
        label = static.direction_label(route_id, direction_id)
        name = static.stop_names[stop_id]
        print(f"T{route.short_name} {name} [{stop_id}] → {label}")
        for dep in departures.compute_departures(
            static, snapshot, key, now, args.limit
        ):
            minutes = int((dep.time - now).total_seconds() // 60)
            delay = "" if dep.delay is None else f" (retard {dep.delay:+d}s)"
            print(
                f"   {dep.time:%H:%M:%S}  {minutes:>3} min  {dep.headsign:<20}"
                f" {dep.source.value}{delay}"
            )


if __name__ == "__main__":
    main()
