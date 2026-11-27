"""Check that the version is the same everywhere it is written."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "tam_montpellier"


def _manifest_version() -> str:
    return json.loads((INTEGRATION / "manifest.json").read_text())["version"]


def test_versions_match() -> None:
    """The manifest, the card, the project and the security policy agree."""
    version = _manifest_version()
    card = (INTEGRATION / "frontend" / "tam-board-card.js").read_text()
    match = re.search(r'^const CARD_VERSION = "([^"]+)";$', card, re.MULTILINE)
    assert match is not None
    assert match[1] == version
    with (ROOT / "pyproject.toml").open("rb") as file:
        assert tomllib.load(file)["project"]["version"] == version
    major, minor, _patch = version.split(".")
    assert f"| Latest ({major}.{minor}.x)" in (ROOT / "SECURITY.md").read_text()
