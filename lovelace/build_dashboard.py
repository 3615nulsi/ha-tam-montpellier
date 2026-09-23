"""Build the TaM Lovelace dashboard (button-card template + one card per stop).

Usage:
    python lovelace/build_dashboard.py \
        sensor.<stop>_minutes_avant_le_prochain_passage ...
Writes lovelace/dashboard.json, to send to the Lovelace config API.
"""

import json
from pathlib import Path
import sys

HERE = Path(__file__).parent
MINUTES_SUFFIX = "_minutes_avant_le_prochain_passage"


def template() -> dict:
    return {
        "show_name": False,
        "show_icon": False,
        "show_state": False,
        "tap_action": {"action": "more-info"},
        "styles": {
            "card": [{"padding": "0"}, {"overflow": "hidden"}],
            "grid": [
                {"grid-template-areas": '"board"'},
                {"grid-template-columns": "1fr"},
                {"grid-template-rows": "auto"},
            ],
            "custom_fields": {
                "board": [{"justify-self": "stretch"}, {"width": "100%"}]
            },
        },
        "extra_styles": (HERE / "tam_board.css").read_text(),
        "custom_fields": {
            "board": "[[[\n" + (HERE / "tam_board.js").read_text() + "]]]"
        },
    }


def card(minutes_entity: str) -> dict:
    alert = minutes_entity.replace(MINUTES_SUFFIX, "_message_de_perturbation")
    return {
        "type": "custom:button-card",
        "template": "tam_stop",
        "entity": minutes_entity,
        "triggers_update": [alert],
        "grid_options": {"columns": "full", "rows": "auto"},
    }


def dashboard(entities: list[str]) -> dict:
    return {
        "button_card_templates": {"tam_stop": template()},
        "views": [
            {
                "title": "Tramway",
                "path": "tram",
                "icon": "mdi:tram",
                "type": "sections",
                "max_columns": 3,
                "sections": [{"type": "grid", "cards": [card(e)]} for e in entities],
            }
        ],
    }


if __name__ == "__main__":
    config = dashboard(sys.argv[1:])
    (HERE / "dashboard.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2)
    )
    print(f"{len(sys.argv) - 1} card(s), {len(json.dumps(config))} bytes")
