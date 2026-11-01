"""Tests of the tram models told by their fleet number."""

from __future__ import annotations

import pytest

from custom_components.tam_montpellier.vehicles import (
    CAF_URBOS,
    CITADIS_302,
    CITADIS_401,
    CITADIS_402,
    VehicleSize,
    vehicle_model,
)


@pytest.mark.parametrize(
    ("vehicle_id", "model"),
    [
        ("2001", CITADIS_401),
        ("2030", CITADIS_401),
        ("2031", CITADIS_302),
        ("2047", CITADIS_302),
        ("2064", CITADIS_302),
        ("2070", CITADIS_402),
        ("2099", CITADIS_402),
        ("2101", CAF_URBOS),
        ("2124", CAF_URBOS),
        ("2000", None),
        ("240", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_vehicle_model(vehicle_id: str | None, model) -> None:
    """Fleet numbers are given by series: the number tells the model."""
    assert vehicle_model(vehicle_id) == model


def test_only_citadis_302_are_short() -> None:
    """Citadis 302 are the short trams, about 10 m shorter than the others."""
    assert CITADIS_302.size is VehicleSize.SHORT
    for model in (CITADIS_401, CITADIS_402, CAF_URBOS):
        assert model.size is VehicleSize.LONG
        assert model.length - CITADIS_302.length > 8
