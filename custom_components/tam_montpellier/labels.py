"""Texts the integration builds itself, from its translations.

Direction choices and device models are made of stop and line names, which
the translation files cannot hold: their wording is kept there, under the
"labels" selector, and filled in here in the language of Home Assistant.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .gtfs_static import StaticData

type Labels = dict[str, str]

_PREFIX = f"component.{DOMAIN}.selector.labels.options."
# Used when a translation is missing.
DEFAULT_LABELS: Labels = {
    "towards": "To {direction} (from {origin})",
    "clockwise": "clockwise",
    "anticlockwise": "anticlockwise",
    "tram_line": "Tram line",
    "tram": "Tram",
    "bus": "Bus",
}


async def async_get_labels(hass: HomeAssistant) -> Labels:
    """Return the texts in the language of Home Assistant."""
    translations = await async_get_translations(
        hass, hass.config.language, "selector", [DOMAIN]
    )
    return {
        **DEFAULT_LABELS,
        **{
            key.removeprefix(_PREFIX): value
            for key, value in translations.items()
            if key.startswith(_PREFIX)
        },
    }


def line_kind(labels: Labels, static: StaticData, route_id: str) -> str:
    """Return what runs on a line, "Tram" or "Bus", in the user's language."""
    return labels[static.line_kind(route_id).lower()]


def rotation(
    labels: Labels, static: StaticData, route_id: str, direction_id: int
) -> str | None:
    """Return the way a circular line runs, None for other lines."""
    clockwise = static.clockwise.get((route_id, direction_id))
    if clockwise is None:
        return None
    return labels["clockwise" if clockwise else "anticlockwise"]
