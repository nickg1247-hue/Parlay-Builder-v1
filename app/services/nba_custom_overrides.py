"""Load/save manual factor overrides for the NBA weighted model."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.config import PROJECT_ROOT

OVERRIDES_PATH = PROJECT_ROOT / "data" / "processed" / "nba_custom_overrides.json"
EXAMPLE_PATH = PROJECT_ROOT / "app" / "data" / "nba_custom_overrides.example.json"

NEUTRAL_STRENGTH = 0.5

MANUAL_FACTOR_KEYS: tuple[str, ...] = (
    "starting_lineup_strength",
    "player_availability_injuries",
    "bench_production",
    "coaching_adjustments",
    "travel_situation",
)

MANUAL_FACTOR_LABELS: dict[str, str] = {
    "starting_lineup_strength": "Starting Lineup Strength",
    "player_availability_injuries": "Player Availability / Injuries",
    "bench_production": "Bench Production",
    "coaching_adjustments": "Coaching / Adjustments",
    "travel_situation": "Travel Situation",
}


def default_manual_factors() -> dict[str, dict[str, float]]:
    return {
        key: {"home": NEUTRAL_STRENGTH, "away": NEUTRAL_STRENGTH}
        for key in MANUAL_FACTOR_KEYS
    }


def normalize_side_block(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {"home": NEUTRAL_STRENGTH, "away": NEUTRAL_STRENGTH}
    home = float(raw.get("home", NEUTRAL_STRENGTH))
    away = float(raw.get("away", NEUTRAL_STRENGTH))
    return {
        "home": min(max(home, 0.0), 1.0),
        "away": min(max(away, 0.0), 1.0),
    }


def normalize_game_override(raw: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """Merge saved override with defaults for all manual factors."""
    out = default_manual_factors()
    if not raw:
        return out
    for key in MANUAL_FACTOR_KEYS:
        if key in raw:
            out[key] = normalize_side_block(raw[key])
    return out


def is_neutral_game_override(factors: dict[str, dict[str, float]]) -> bool:
    for key in MANUAL_FACTOR_KEYS:
        block = factors.get(key) or {}
        if abs(float(block.get("home", NEUTRAL_STRENGTH)) - NEUTRAL_STRENGTH) > 1e-6:
            return False
        if abs(float(block.get("away", NEUTRAL_STRENGTH)) - NEUTRAL_STRENGTH) > 1e-6:
            return False
    return True


def load_overrides_document() -> dict[str, Any]:
    if OVERRIDES_PATH.exists():
        try:
            return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    if EXAMPLE_PATH.exists():
        try:
            doc = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
            doc.pop("_comment", None)
            return doc
        except (json.JSONDecodeError, OSError):
            pass
    return {"by_date": {}}


def save_overrides_document(doc: dict[str, Any]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def get_overrides_for_date(game_date: date) -> dict[str, dict[str, Any]]:
    doc = load_overrides_document()
    by_date = doc.get("by_date") or {}
    day = by_date.get(game_date.isoformat()) or {}
    return {str(k): v for k, v in day.items()}


def get_game_override(game_date: date | None, game_id: str) -> dict[str, Any]:
    if not game_date:
        return {}
    day = get_overrides_for_date(game_date)
    return day.get(str(game_id)) or {}


def save_overrides_for_date(
    game_date: date,
    games: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Persist overrides for one slate date; drop all-neutral games."""
    doc = load_overrides_document()
    by_date: dict[str, dict[str, Any]] = dict(doc.get("by_date") or {})
    cleaned: dict[str, Any] = {}
    for game_id, raw in games.items():
        factors = normalize_game_override(raw if isinstance(raw, dict) else None)
        if not is_neutral_game_override(factors):
            cleaned[str(game_id)] = factors
    if cleaned:
        by_date[game_date.isoformat()] = cleaned
    elif game_date.isoformat() in by_date:
        del by_date[game_date.isoformat()]
    doc["by_date"] = by_date
    save_overrides_document(doc)
    return doc


def factors_schema() -> list[dict[str, str]]:
    return [{"key": k, "label": MANUAL_FACTOR_LABELS[k]} for k in MANUAL_FACTOR_KEYS]
