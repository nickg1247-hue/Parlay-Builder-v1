"""MLB run line cover model — margin regression + Normal CDF at book line."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from app.config import PROJECT_ROOT
from app.features.mlb_pregame import FEATURE_COLUMNS_WAVE1, build_features_for_history
from app.models.mlb_baseline import load_games, time_split
from app.odds.spread_math import (
    model_prob_away_cover,
    model_prob_home_cover,
    side_covers,
)

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "mlb_spread_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_spread_metrics.json"

PROXY_HOME_SPREAD = -1.5
PROXY_AWAY_SPREAD = 1.5
FEATURE_COLUMNS = list(FEATURE_COLUMNS_WAVE1)


def run_training() -> dict[str, Any]:
    raw = load_games()
    feat = build_features_for_history(raw)
    feat = feat[feat["home_score"].notna() & feat["away_score"].notna()].copy()
    feat["margin"] = feat["home_score"].astype(float) - feat["away_score"].astype(float)

    train = feat[feat["season"].isin([2023, 2024])].copy()
    test = feat[feat["season"] == 2025].copy()

    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(train[FEATURE_COLUMNS].values, train["margin"].values)
    pred_test = reg.predict(test[FEATURE_COLUMNS].values)
    residuals = test["margin"].values - pred_test
    margin_std = float(np.std(residuals, ddof=1))
    if math.isnan(margin_std) or margin_std <= 0:
        margin_std = 3.0

    mae = float(mean_absolute_error(test["margin"], pred_test))

    proxy_home_actual = [
        side_covers("home", r.home_score, r.away_score, PROXY_HOME_SPREAD, PROXY_AWAY_SPREAD)
        for r in test.itertuples(index=False)
    ]
    proxy_home_pred = [
        model_prob_home_cover(float(p), margin_std, PROXY_HOME_SPREAD) >= 0.5
        for p in pred_test
    ]
    proxy_acc = float(
        sum(a == b for a, b in zip(proxy_home_actual, proxy_home_pred)) / len(test)
    )

    artifact = {
        "model": reg,
        "model_version": "v1_margin_gbr_normal",
        "feature_columns": FEATURE_COLUMNS,
        "margin_std": margin_std,
        "proxy_lines": {
            "home_spread_point": PROXY_HOME_SPREAD,
            "away_spread_point": PROXY_AWAY_SPREAD,
        },
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_ARTIFACT)

    results = {
        "train_seasons": [2023, 2024],
        "holdout_season": 2025,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "holdout_mae_margin": round(mae, 4),
        "holdout_margin_std": round(margin_std, 4),
        "proxy_cover_accuracy_home": round(proxy_acc, 4),
        "note": (
            "No free historical run-line CSV; holdout eval uses ±1.5 proxy. "
            "Live board uses actual book lines from Odds API."
        ),
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_spread_artifact() -> dict[str, Any]:
    if not MODEL_ARTIFACT.exists():
        raise FileNotFoundError(
            f"Spread model not found at {MODEL_ARTIFACT}. "
            "Run scripts/train_mlb_spread.py first."
        )
    return joblib.load(MODEL_ARTIFACT)


def predict_spread_covers(df: pd.DataFrame) -> pd.DataFrame:
    """Add predicted margin and cover probs at each row's book spread (if present)."""
    artifact = load_spread_artifact()
    cols = artifact["feature_columns"]
    std = float(artifact["margin_std"])
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Spread model missing feature columns: {missing}")

    out = df.copy()
    margins = artifact["model"].predict(out[cols].values)
    out["model_margin"] = margins
    home_probs: list[float | None] = []
    away_probs: list[float | None] = []
    for row, margin in zip(out.itertuples(index=False), margins):
        hp = getattr(row, "home_spread_point", None)
        ap = getattr(row, "away_spread_point", None)
        if hp is not None and not (isinstance(hp, float) and math.isnan(hp)):
            home_probs.append(
                round(model_prob_home_cover(float(margin), std, float(hp)), 4)
            )
        else:
            home_probs.append(None)
        if ap is not None and not (isinstance(ap, float) and math.isnan(ap)):
            away_probs.append(
                round(model_prob_away_cover(float(margin), std, float(ap)), 4)
            )
        else:
            away_probs.append(None)
    out["model_prob_home_cover"] = home_probs
    out["model_prob_away_cover"] = away_probs
    return out
