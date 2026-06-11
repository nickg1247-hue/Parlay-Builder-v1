"""Load, save, and adjust global NBA custom model factor weights."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.config import PROJECT_ROOT
from app.features.nba_custom_factors import FACTOR_LABELS

WEIGHTS_PATH = PROJECT_ROOT / "app" / "data" / "nba_custom_weights.json"

FACTOR_ORDER: tuple[str, ...] = tuple(FACTOR_LABELS.keys())

DEFAULT_FACTORS: dict[str, float] = {
    "team_offensive_rating": 0.15,
    "team_defensive_rating": 0.15,
    "starting_lineup_strength": 0.12,
    "player_availability_injuries": 0.12,
    "recent_form_last10": 0.08,
    "home_court_advantage": 0.07,
    "bench_production": 0.06,
    "matchup_advantages": 0.05,
    "rest_fatigue": 0.04,
    "pace_of_play_matchup": 0.03,
    "rebounding_edge": 0.03,
    "turnover_differential": 0.03,
    "three_point_shooting_efficiency": 0.03,
    "free_throw_rate": 0.02,
    "travel_situation": 0.01,
    "coaching_adjustments": 0.01,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "model_id": "custom_weighted_v1",
    "description": "User-defined weighted factor model for NBA moneyline",
    "score_scale": 4.0,
    "factors": DEFAULT_FACTORS,
}

MIN_WEIGHT_PCT = 1
MAX_WEIGHT_PCT = 40


def _pct_weights(factors: dict[str, float]) -> dict[str, int]:
    return {key: int(round(float(factors.get(key, 0.0)) * 100)) for key in FACTOR_ORDER}


def _normalize_pct_weights(pct: dict[str, int]) -> dict[str, int]:
    """Integer percent weights that sum to 100."""
    total = sum(pct.get(k, 0) for k in FACTOR_ORDER)
    if total <= 0:
        return _pct_weights(DEFAULT_FACTORS)
    scaled = {k: max(MIN_WEIGHT_PCT, int(round(pct.get(k, 0) * 100 / total))) for k in FACTOR_ORDER}
    drift = 100 - sum(scaled.values())
    if drift:
        adjustable = [k for k in FACTOR_ORDER if scaled[k] > MIN_WEIGHT_PCT or drift > 0]
        idx = 0
        while drift != 0 and adjustable:
            key = adjustable[idx % len(adjustable)]
            if drift > 0 and scaled[key] < MAX_WEIGHT_PCT:
                scaled[key] += 1
                drift -= 1
            elif drift < 0 and scaled[key] > MIN_WEIGHT_PCT:
                scaled[key] -= 1
                drift += 1
            idx += 1
            if idx > 500:
                break
    return scaled


def adjust_weight_pct(pct: dict[str, int], key: str, delta: int) -> dict[str, int]:
    if key not in FACTOR_ORDER:
        raise ValueError(f"Unknown factor: {key}")
    out = dict(pct)
    out[key] = max(MIN_WEIGHT_PCT, min(MAX_WEIGHT_PCT, int(out.get(key, 0)) + int(delta)))
    excess = sum(out.values()) - 100
    if excess == 0:
        return out
    step = -1 if excess > 0 else 1
    remaining = abs(excess)
    others = sorted(
        [k for k in FACTOR_ORDER if k != key],
        key=lambda k: out[k],
        reverse=(excess > 0),
    )
    idx = 0
    guard = 0
    while remaining > 0 and others and guard < 1000:
        other = others[idx % len(others)]
        next_val = out[other] + step
        if MIN_WEIGHT_PCT <= next_val <= MAX_WEIGHT_PCT:
            out[other] = next_val
            remaining -= 1
        idx += 1
        guard += 1
    if sum(out.values()) != 100:
        out = _normalize_pct_weights(out)
    return out


def pct_to_fraction(pct: dict[str, int]) -> dict[str, float]:
    normalized = _normalize_pct_weights(pct)
    return {key: round(normalized[key] / 100.0, 4) for key in FACTOR_ORDER}


def load_custom_weights_config() -> dict[str, Any]:
    if WEIGHTS_PATH.exists():
        try:
            raw = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
            factors = raw.get("factors") or {}
            pct = _normalize_pct_weights(_pct_weights(factors))
            raw["factors"] = pct_to_fraction(pct)
            return raw
        except (json.JSONDecodeError, OSError):
            pass
    return deepcopy(DEFAULT_CONFIG)


def save_custom_weights_config(config: dict[str, Any]) -> dict[str, Any]:
    factors_in = config.get("factors") or {}
    if not isinstance(factors_in, dict):
        raise ValueError("factors must be an object")
    unknown = [k for k in factors_in if k not in FACTOR_ORDER]
    if unknown:
        raise ValueError(f"Unknown factor keys: {', '.join(unknown)}")
    pct = _normalize_pct_weights(
        {k: int(round(float(factors_in.get(k, 0)) * 100)) for k in FACTOR_ORDER}
    )
    out = {
        "model_id": config.get("model_id", DEFAULT_CONFIG["model_id"]),
        "description": config.get("description", DEFAULT_CONFIG["description"]),
        "score_scale": float(config.get("score_scale", DEFAULT_CONFIG["score_scale"])),
        "factors": pct_to_fraction(pct),
    }
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def weights_payload() -> dict[str, Any]:
    cfg = load_custom_weights_config()
    pct = _normalize_pct_weights(_pct_weights(cfg["factors"]))
    return {
        "model_id": cfg.get("model_id"),
        "score_scale": cfg.get("score_scale"),
        "total_pct": sum(pct.values()),
        "factors": [
            {
                "key": key,
                "label": FACTOR_LABELS[key],
                "weight_pct": pct[key],
                "weight": round(pct[key] / 100.0, 4),
            }
            for key in FACTOR_ORDER
        ],
    }


def default_weights_payload() -> dict[str, Any]:
    save_custom_weights_config(deepcopy(DEFAULT_CONFIG))
    return weights_payload()
