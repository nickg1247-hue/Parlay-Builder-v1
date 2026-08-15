"""NFL game totals (O/U points) — GBR expected total + Normal over probability."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import log_loss, mean_absolute_error

from app.config import PROJECT_ROOT
from app.features.nfl_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_totals_features_for_history,
    build_totals_features_for_slate,
)
from app.models.nfl_baseline import HOLDOUT_SEASON, REGRESSION_TRAIN_SEASONS, load_games, time_split_regression
from app.odds.spread_math import norm_cdf

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "nfl_totals_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_totals_metrics.json"
ACTIVE_TOTALS_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_nfl_totals_model.json"
DEFAULT_TOTAL_STD = 13.0


def actual_went_over(total_pts: float, ou_line: float) -> int:
    if ou_line % 1 == 0.5:
        return int(total_pts > ou_line)
    return int(total_pts >= ou_line)


def prob_over_normal(expected_total: float, std: float, ou_line: float) -> float:
    mu = max(float(expected_total), 1.0)
    sigma = max(float(std), 1.0)
    if ou_line % 1 == 0.5:
        return float(1.0 - norm_cdf(float(ou_line), mu, sigma))
    return float(1.0 - norm_cdf(float(ou_line) - 0.5, mu, sigma))


def totals_production_gate_passes(
    model_log_loss: float | None,
    league_log_loss: float | None,
    model_mae: float,
    league_mae: float,
) -> bool:
    if model_log_loss is None or league_log_loss is None:
        return False
    if model_log_loss > league_log_loss:
        return False
    return model_mae <= league_mae


def run_training() -> dict[str, Any]:
    raw = load_games()
    raw = raw[raw["home_score"].notna() & raw["away_score"].notna()].copy()
    raw["total_points"] = raw["home_score"].astype(float) + raw["away_score"].astype(float)
    feat = build_totals_features_for_history(raw)
    if "total_points" not in feat.columns:
        feat = feat.merge(raw[["game_id", "total_points"]], on="game_id", how="left")
    train, test = time_split_regression(feat)
    league_avg_total = float(train["total_points"].median())
    if math.isnan(league_avg_total):
        league_avg_total = 44.5
    proxy_ou_line = round(league_avg_total * 2) / 2
    if proxy_ou_line == int(proxy_ou_line):
        proxy_ou_line += 0.5

    reg = GradientBoostingRegressor(
        n_estimators=120, max_depth=3, learning_rate=0.08, random_state=42
    )
    reg.fit(train[TOTALS_FEATURE_COLUMNS].values, train["total_points"].values)
    pred_test = reg.predict(test[TOTALS_FEATURE_COLUMNS].values)
    residuals = test["total_points"].values - pred_test
    total_std = float(np.std(residuals, ddof=1))
    if math.isnan(total_std) or total_std <= 0:
        total_std = DEFAULT_TOTAL_STD
    mae_model = float(mean_absolute_error(test["total_points"], pred_test))
    mae_league = float(
        mean_absolute_error(test["total_points"], np.full(len(test), league_avg_total))
    )
    y_over = np.array([actual_went_over(float(t), proxy_ou_line) for t in test["total_points"]])
    model_probs = np.array([prob_over_normal(float(p), total_std, proxy_ou_line) for p in pred_test])
    league_probs = np.array(
        [prob_over_normal(league_avg_total, total_std, proxy_ou_line) for _ in y_over]
    )
    model_ll = float(log_loss(y_over, np.clip(model_probs, 1e-6, 1 - 1e-6)))
    league_ll = float(log_loss(y_over, np.clip(league_probs, 1e-6, 1 - 1e-6)))
    gate_passes = totals_production_gate_passes(model_ll, league_ll, mae_model, mae_league)

    artifact = {
        "model": reg,
        "model_version": "v1_gbr_normal",
        "feature_columns": list(TOTALS_FEATURE_COLUMNS),
        "total_std": total_std,
        "league_avg_total": league_avg_total,
        "proxy_ou_line": proxy_ou_line,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_ARTIFACT)
    ACTIVE_TOTALS_MANIFEST.write_text(
        json.dumps(
            {
                "track": "nfl_totals",
                "model_version": artifact["model_version"],
                "path": "data/processed/nfl_totals_model.joblib",
                "production_ready": gate_passes,
                "promoted_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    results = {
        "train_seasons": list(REGRESSION_TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "proxy_ou_line": proxy_ou_line,
        "league_avg_total": round(league_avg_total, 2),
        "holdout_mae_total_pts": round(mae_model, 3),
        "league_avg_mae_total_pts": round(mae_league, 3),
        "log_loss_model": round(model_ll, 4),
        "log_loss_league_avg": round(league_ll, 4),
        "totals_production_gate_passes": gate_passes,
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_totals_artifact() -> dict[str, Any]:
    if ACTIVE_TOTALS_MANIFEST.exists():
        manifest = json.loads(ACTIVE_TOTALS_MANIFEST.read_text(encoding="utf-8"))
        path = PROJECT_ROOT / manifest["path"]
        if path.exists():
            return joblib.load(path)
    if MODEL_ARTIFACT.exists():
        return joblib.load(MODEL_ARTIFACT)
    raise FileNotFoundError(f"No NFL totals model at {MODEL_ARTIFACT}.")


def predict_expected_total(df: pd.DataFrame) -> np.ndarray:
    artifact = load_totals_artifact()
    cols = artifact["feature_columns"]
    prepared = (
        df
        if "home_season_pts_for" in df.columns and "elo_home_pre" in df.columns
        else build_totals_features_for_slate(df)
    )
    preds = artifact["model"].predict(prepared[cols].values)
    if len(prepared) == len(df):
        return preds
    pred_by_id = dict(zip(prepared["game_id"].astype(str), preds))
    return df["game_id"].astype(str).map(pred_by_id).to_numpy()


def enrich_totals_columns(df: pd.DataFrame) -> pd.DataFrame:
    artifact = load_totals_artifact()
    std = float(artifact.get("total_std", DEFAULT_TOTAL_STD))
    proxy_line = float(artifact.get("proxy_ou_line", artifact.get("league_avg_total", 44.5)))
    out = df.copy()
    expected = predict_expected_total(out)
    out["expected_total_pts"] = expected
    probs: list[float] = []
    for exp, row in zip(expected, out.itertuples(index=False)):
        line = getattr(row, "ou_line", None)
        if line is None or (isinstance(line, float) and math.isnan(line)):
            line = proxy_line
        probs.append(round(prob_over_normal(float(exp), std, float(line)), 4))
    out["model_prob_over"] = probs
    return out
