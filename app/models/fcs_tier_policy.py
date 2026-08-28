"""Immutable public confidence policy for the experimental FCS model."""
from __future__ import annotations

import json
from typing import Any

from app.config import PROJECT_ROOT

POLICY_PATH = PROJECT_ROOT / "data/processed/fcs_tier_policy.json"
PUBLIC_CAP = 0.90
TIERS = (
    ("toss_up", "Toss-up", 0.50, 0.55),
    ("lean", "Lean", 0.55, 0.70),
    ("strong", "Strong", 0.70, 0.85),
    ("elite", "Elite", 0.85, 0.90),
)


def load_tier_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def cap_home_probability(raw_home: float) -> tuple[float, bool]:
    value = float(raw_home)
    favorite = max(value, 1 - value)
    if favorite <= PUBLIC_CAP:
        return value, False
    return (PUBLIC_CAP if value >= 0.5 else 1 - PUBLIC_CAP), True


def tier_key_for_probability(favorite_probability: float) -> str:
    probability = float(favorite_probability)
    if probability < 0.50 or probability > 0.90:
        raise ValueError("FCS public favorite probability must be within [0.50, 0.90]")
    for key, _label, lower, upper in TIERS:
        if lower <= probability < upper or (
            key == "elite" and lower <= probability <= upper
        ):
            return key
    raise ValueError(f"No FCS tier for probability {probability}")


def tier_is_validated(row: dict[str, Any], minimum_sample: int) -> bool:
    target = float(row["accuracy_target"])
    seasons = row.get("by_season") or {}
    return bool(seasons) and all(
        int(item.get("games") or 0) >= minimum_sample
        and item.get("accuracy") is not None
        and float(item["accuracy"]) >= target
        for item in seasons.values()
    )


def tier_decision(
    favorite_probability: float,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = policy or load_tier_policy()
    key = tier_key_for_probability(favorite_probability)
    row = active["tiers"][key]
    validated = tier_is_validated(row, int(active["minimum_oos_sample"]))
    return {
        "candidate_tier": row["label"],
        "tier_key": key,
        "tier_validated": validated,
        "public_tier_label": row["label"] if validated else None,
        "tier_status_label": (
            row["label"]
            if validated
            else f"FCS Beta · {row['label']} tier unvalidated"
        ),
        "tier_validation_reason": row["reason"],
        "tier_policy_version": active["policy_version"],
        "tier_evidence": {
            "minimum_sample": active["minimum_oos_sample"],
            "pooled": row["pooled"],
            "by_season": row["by_season"],
        },
    }
