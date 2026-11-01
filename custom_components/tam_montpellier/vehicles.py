"""Tram models of the TaM fleet, told by their fleet number.

The real-time feeds name the tram running a trip by its fleet number only.
The numbers are given by series, so the model follows from the number:

* 2001-2030: Alstom Citadis 401, 40.9 m (line 1);
* 2031-2064: Alstom Citadis 302, 32.5 m, the only short trams (lines 2, 4);
* 2065-2099: Alstom Citadis 402, 42 m (lines 1 to 4);
* 2101-2199: CAF Urbos, 43 m (lines 1, 5).

This module has no Home Assistant dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VehicleSize(StrEnum):
    """Length class of a tram, what passengers on the platform care about."""

    SHORT = "short"
    LONG = "long"


@dataclass(frozen=True, slots=True)
class VehicleModel:
    """A tram model."""

    key: str
    """Identifier, the state of the "next tram" sensor."""
    name: str
    length: float
    """Length in metres."""
    size: VehicleSize


CITADIS_401 = VehicleModel("citadis_401", "Citadis 401", 40.9, VehicleSize.LONG)
CITADIS_302 = VehicleModel("citadis_302", "Citadis 302", 32.5, VehicleSize.SHORT)
CITADIS_402 = VehicleModel("citadis_402", "Citadis 402", 42.0, VehicleSize.LONG)
CAF_URBOS = VehicleModel("caf_urbos", "CAF Urbos", 43.0, VehicleSize.LONG)

MODELS: tuple[VehicleModel, ...] = (CITADIS_401, CITADIS_302, CITADIS_402, CAF_URBOS)

_SERIES: tuple[tuple[int, int, VehicleModel], ...] = (
    (2001, 2030, CITADIS_401),
    (2031, 2064, CITADIS_302),
    (2065, 2099, CITADIS_402),
    (2101, 2199, CAF_URBOS),
)


def vehicle_model(vehicle_id: str | None) -> VehicleModel | None:
    """Return the model of a tram from its fleet number, None when unknown."""
    if not vehicle_id or not vehicle_id.isdigit():
        return None
    number = int(vehicle_id)
    for first, last, model in _SERIES:
        if first <= number <= last:
            return model
    return None
