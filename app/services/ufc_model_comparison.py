"""Load and run UFC model A/B comparison (matchup engine vs baseline)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.features.ufc_pregame import build_features_for_history
from app.models.ufc_baseline import (
    HOLDOUT_SEASON,
    compute_metrics,
    load_fights,
    load_model_artifact,
    predict_home_win_proba,
)
from app.models.ufc_matchup_engine import predict_matchup

COMPARISON_JSON = PROJECT_ROOT / "data" / "processed" / "ufc_model_comparison.json"
DEFAULT_LABEL = "Matchup engine v1"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_model_comparison(*, write_cache: bool = True) -> dict[str, Any]:
    fights = load_fights()
    holdout = fights[fights["season"] == HOLDOUT_SEASON].copy()
    if holdout.empty:
        return {
            "status": "error",
            "message": f"No holdout fights for season {HOLDOUT_SEASON}",
            "generated_at": _iso_now(),
            "active_model_label": DEFAULT_LABEL,
        }

    feat = build_features_for_history(fights)
    holdout_feat = feat[feat["season"] == HOLDOUT_SEASON].merge(
        holdout[["fight_id", "weight_class"]],
        on="fight_id",
        how="left",
    )

    try:
        artifact = load_model_artifact()
        baseline_version = str(artifact.get("model_version", "ufc_baseline"))
    except FileNotFoundError:
        baseline_version = "ufc_baseline_missing"

    baseline_probs = np.array(predict_home_win_proba(holdout), dtype=float)
    matchup_probs: list[float] = []
    for row in holdout_feat.itertuples(index=False):
        fight_dict = {
            "home_team": row.home_team,
            "away_team": row.away_team,
            "weight_class": getattr(row, "weight_class", "") or "",
        }
        slate_day = pd.to_datetime(row.date).date()
        try:
            out = predict_matchup(
                fight_dict,
                slate_day,
                feature_row=row._asdict(),
                history_df=fights,
            )
            matchup_probs.append(float(out["probHome"]))
        except (ValueError, TypeError, KeyError):
            matchup_probs.append(float("nan"))

    matchup_probs_arr = np.array(matchup_probs, dtype=float)
    y = holdout["home_win"].astype(int).values
    valid = ~np.isnan(matchup_probs_arr)
    y_valid = y[valid]
    baseline_valid = baseline_probs[valid]
    matchup_valid = matchup_probs_arr[valid]

    baseline_m = compute_metrics("baseline_logistic", y_valid, baseline_valid)
    matchup_m = compute_metrics("matchup_engine_v1", y_valid, matchup_valid)

    delta_ll = round(matchup_m.log_loss - baseline_m.log_loss, 6)
    active = "matchup_engine_v1" if matchup_m.log_loss <= baseline_m.log_loss else baseline_version
    active_label = (
        "Matchup engine v1"
        if active == "matchup_engine_v1"
        else "Baseline logistic (Platt)"
    )

    payload: dict[str, Any] = {
        "generated_at": _iso_now(),
        "holdout_season": HOLDOUT_SEASON,
        "holdout_fights": int(len(holdout)),
        "evaluated_fights": int(valid.sum()),
        "baseline": {
            "model_id": baseline_version,
            "label": "Baseline logistic (Platt)",
            "log_loss": baseline_m.log_loss,
            "brier": baseline_m.brier,
            "accuracy": baseline_m.accuracy,
        },
        "matchup": {
            "model_id": "matchup_engine_v1",
            "label": "Matchup engine v1",
            "log_loss": matchup_m.log_loss,
            "brier": matchup_m.brier,
            "accuracy": matchup_m.accuracy,
        },
        "delta_log_loss": delta_ll,
        "matchup_beats_baseline": matchup_m.log_loss < baseline_m.log_loss,
        "active_model": active,
        "active_model_label": active_label,
        "status": "ok",
    }

    if write_cache:
        COMPARISON_JSON.parent.mkdir(parents=True, exist_ok=True)
        COMPARISON_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload


def load_model_comparison() -> dict[str, Any]:
    if not COMPARISON_JSON.exists():
        return {
            "status": "missing",
            "active_model_label": DEFAULT_LABEL,
            "active_model": "matchup_engine_v1",
        }
    try:
        data = json.loads(COMPARISON_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "status": "error",
            "active_model_label": DEFAULT_LABEL,
            "active_model": "matchup_engine_v1",
        }
    data.setdefault("active_model_label", DEFAULT_LABEL)
    return data
