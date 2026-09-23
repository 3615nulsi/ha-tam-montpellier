"""Fixtures for the TaM Montpellier tests.

The synthetic GTFS mimics the quirks of the real TaM feeds: every trip exists
twice (an inactive ``100`` copy and the active ``U_100-7`` one), only
``calendar_dates.txt`` is provided, and the real-time feed references the
prefixed trip ids.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
import io
from pathlib import Path
from unittest.mock import patch
import zipfile
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2
import pytest

from homeassistant.core import HomeAssistant

PARIS = ZoneInfo("Europe/Paris")
SERVICE_DAY = "20260923"

# Line 1 direction 0: A -> B -> C -> D (3 min between stops), every 10 minutes
# from 08:00 to 09:00, plus a night trip leaving A at 24:30.
STOPS = {
    "A": "Alpha",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "X": "Bus stop",
}
LINE_1_DIR_0 = ["A", "B", "C", "D"]
DEPARTURE_MINUTES = [8 * 60 + 10 * i for i in range(7)] + [24 * 60 + 30]


def trip_id(index: int, direction: int = 0) -> str:
    """Return the active (real-time) id of a synthetic trip."""
    return f"U_{100 + 100 * direction + index}-7"


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def build_gtfs() -> bytes:
    """Build the synthetic GTFS archive."""
    files: dict[str, list[str]] = {
        "agency.txt": [
            "agency_id,agency_name,agency_url,agency_timezone",
            "TAM,TaM,https://www.tam-voyages.com,Europe/Paris",
        ],
        "routes.txt": [
            "route_id,route_short_name,route_long_name,route_type,agency_id,route_color,route_text_color",
            "1,1,Alpha - Delta,0,TAM,005CA9,FFFFFF",
            "10,10,Bus line,3,TAM,FBBA00,000000",
        ],
        "stops.txt": ["stop_id,stop_name,stop_lat,stop_lon"]
        + [f"{stop_id},{name},43.6,3.87" for stop_id, name in STOPS.items()],
        "calendar_dates.txt": [
            "service_id,date,exception_type",
            f"U_07_1,{SERVICE_DAY},1",
            "BASE,20261201,1",
            f"BUS,{SERVICE_DAY},1",
        ],
        "trips.txt": [
            "route_id,service_id,trip_id,trip_headsign,direction_id",
            "10,BUS,bus-1,Bus terminus,0",
        ],
        "stop_times.txt": [
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence",
            "bus-1,08:00:00,08:00:00,X,1",
            "bus-1,08:05:00,08:05:00,B,2",
        ],
    }
    for direction, stops in ((0, LINE_1_DIR_0), (1, LINE_1_DIR_0[::-1])):
        headsign = STOPS[stops[-1]]
        for index, start in enumerate(DEPARTURE_MINUTES):
            active = trip_id(index, direction)
            inactive = active.removeprefix("U_").removesuffix("-7")
            for tid, service in ((active, "U_07_1"), (inactive, "BASE")):
                files["trips.txt"].append(f"1,{service},{tid},{headsign},{direction}")
                for seq, stop_id in enumerate(stops):
                    time = _fmt(start + 3 * seq)
                    files["stop_times.txt"].append(
                        f"{tid},{time},{time},{stop_id},{seq + 1}"
                    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, lines in files.items():
            archive.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


def paris(hour: int, minute: int, second: int = 0, day: int = 23) -> datetime:
    """Return an aware datetime on the synthetic service day."""
    return datetime(2026, 9, day, hour, minute, second, tzinfo=PARIS)


def build_trip_updates(*trips: dict) -> bytes:
    """Build a GTFS-RT TripUpdate feed.

    Each trip is a dict with ``trip_id``, ``route_id``, ``direction_id`` and
    ``stops``: a list of ``(stop_id, datetime | None, delay | None, skipped)``.
    ``canceled=True`` marks a canceled trip.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(paris(8, 0).timestamp())
    for index, trip in enumerate(trips):
        entity = feed.entity.add()
        entity.id = str(index)
        update = entity.trip_update
        update.trip.trip_id = trip["trip_id"]
        update.trip.route_id = trip.get("route_id", "1")
        update.trip.direction_id = trip.get("direction_id", 0)
        if trip.get("canceled"):
            update.trip.schedule_relationship = (
                gtfs_realtime_pb2.TripDescriptor.CANCELED
            )
        for stop_id, when, delay, skipped in trip.get("stops", ()):
            stu = update.stop_time_update.add()
            stu.stop_id = stop_id
            if skipped:
                stu.schedule_relationship = (
                    gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED
                )
                continue
            if when is not None:
                stu.arrival.time = int(when.timestamp())
                stu.departure.time = int(when.timestamp())
            if delay is not None:
                stu.arrival.delay = delay
                stu.departure.delay = delay
    return feed.SerializeToString()


@pytest.fixture(name="gtfs_zip")
def gtfs_zip_fixture(tmp_path: Path) -> Path:
    """Write the synthetic GTFS archive to disk."""
    path = tmp_path / "gtfs.zip"
    path.write_bytes(build_gtfs())
    return path


@pytest.fixture
def isolated_storage(hass: HomeAssistant, tmp_path: Path) -> Generator[None]:
    """Keep the cached GTFS archive out of the shared test config directory.

    Used together with ``enable_custom_integrations`` by the tests that run
    Home Assistant.
    """
    storage = tmp_path / "config"
    storage.mkdir()
    with patch.object(hass.config, "config_dir", str(storage)):
        yield


def build_alerts(*alerts: dict) -> bytes:
    """Build a GTFS-RT Alert feed.

    Each alert is a dict with ``id``, ``informed`` (a list of dicts with
    ``agency_id``, ``route_id``, ``direction_id`` or ``stop_id``), optional
    ``periods`` (``(start, end)`` datetimes), ``header`` and ``description``.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for alert in alerts:
        entity = feed.entity.add()
        entity.id = alert["id"]
        for informed in alert.get("informed", ()):
            selector = entity.alert.informed_entity.add()
            for field, value in informed.items():
                setattr(selector, field, value)
        for start, end in alert.get("periods", ()):
            period = entity.alert.active_period.add()
            if start is not None:
                period.start = int(start.timestamp())
            if end is not None:
                period.end = int(end.timestamp())
        for field in ("header", "description"):
            if field in alert:
                getattr(entity.alert, f"{field}_text").translation.add(
                    text=alert[field], language="fr"
                )
    return feed.SerializeToString()
