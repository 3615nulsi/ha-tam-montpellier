"""Tests of the static GTFS loading helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
import zipfile

from custom_components.tam_montpellier import gtfs_static
from custom_components.tam_montpellier.gtfs_static import _read_columns, load_static


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
