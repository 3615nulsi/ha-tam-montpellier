"""Tests of the service alerts parsing and matching."""

from __future__ import annotations

from custom_components.tam_montpellier.alerts import alerts_for, parse_alerts

from .conftest import build_alerts, paris

KEY = ("B", "1", 0)
NOW = paris(8, 12)


def _ids(payload: bytes, key=KEY, now=NOW) -> list[str]:
    return [alert.alert_id for alert in alerts_for(parse_alerts(payload), key, now)]


def test_alert_matching() -> None:
    """Alerts apply to their line, direction, stop or the whole network."""
    payload = build_alerts(
        {"id": "line", "informed": [{"route_id": "1"}]},
        {"id": "other_line", "informed": [{"route_id": "13"}]},
        {"id": "direction", "informed": [{"route_id": "1", "direction_id": 1}]},
        {"id": "stop", "informed": [{"stop_id": "B"}]},
        {"id": "line_other_stop", "informed": [{"route_id": "1", "stop_id": "C"}]},
        {"id": "network", "informed": [{"agency_id": "TAM"}]},
    )
    assert _ids(payload) == ["line", "stop", "network"]


def test_alert_periods() -> None:
    """Only alerts in effect are reported."""
    payload = build_alerts(
        {
            "id": "now",
            "informed": [{"route_id": "1"}],
            "periods": [(paris(8, 0), paris(9, 0))],
        },
        {
            "id": "later",
            "informed": [{"route_id": "1"}],
            "periods": [(paris(10, 0), None)],
        },
        {
            "id": "over",
            "informed": [{"route_id": "1"}],
            "periods": [(None, paris(8, 0))],
        },
        {"id": "always", "informed": [{"route_id": "1"}]},
    )
    assert _ids(payload) == ["now", "always"]
    alert = parse_alerts(payload)[0]
    assert alert.current_end(NOW) == paris(9, 0)


def test_alert_texts() -> None:
    """The readable description is preferred over the internal header code."""
    payload = build_alerts(
        {
            "id": "a",
            "informed": [{"route_id": "1"}],
            "header": "  TR/L1/Dev/CODE  ",
            "description": "TRAVAUX : arrêt Bravo non desservi.",
        },
        {"id": "b", "informed": [{"route_id": "1"}], "header": "Only a header"},
    )
    first, second = parse_alerts(payload)
    assert first.header == "TR/L1/Dev/CODE"
    assert first.description == "TRAVAUX : arrêt Bravo non desservi."
    assert second.description == "Only a header"
