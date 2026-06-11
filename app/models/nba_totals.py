"""NBA game totals (O/U points) — GBR expected total + Normal over probability."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import log_loss, mean_absolute_error

from app.config import PROJECT_ROOT
from app.features.nba_totals_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_features_for_history,
    build_features_for_slate,
)
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nba_baseline import HOLDOUT_SEASON, TRAIN_SEASONS, load_games, time_split
from app.odds.nba_odds_free import ODDS_2026_CSV
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_math import market_probs_from_american_totals
from app.odds.spread_math import norm_cdf
from app.odds.team_aliases import is_valid_american_odds

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "nba_totals_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nba_totals_metrics.json"
ACTIVE_TOTALS_MANIFEST = (
    PROJECT_ROOT / "data" / "processed" / "active_nba_totals_model.json"
)

LEAGUE_AVG_TOTAL = 220.0
DEFAULT_TOTAL_STD = 18.0
TOTALS_DISCLAIMER = (
    "NBA O/U model is experimental (GBR + Normal over prob); separate gate from moneyline."
)


def actual_went_over(total_pts: float, ou_line: float) -> int:
    if ou_line % 1 == 0.5:
        return int(total_pts > ou_line)
    return int(total_pts >= ou_line)


def prob_over_normal(expected_total: float, std: float, ou_line: float) -> float:
    """P(combined points over the book line) under Normal(expected_total, std).

    Uses continuous Normal CDF (documented in TOTALS_NBA.md) — better fit than Poisson
    for NBA combined scores (~220 pts, moderate variance).
    """
    mu = max(float(expected_total), 1.0)
    sigma = max(float(std), 1.0)
    if ou_line % 1 == 0.5:
        return float(1.0 - norm_cdf(float(ou_line), mu, sigma))
    return float(1.0 - norm_cdf(float(ou_line) - 0.5, mu, sigma))


def totals_production_gate_passes(
    model_log_loss: float | None,
    market_log_loss: float | None,
    model_mae: float,
    league_mae: float,
) -> bool:
    if model_log_loss is None or market_log_loss is None:
        return False
    if model_log_loss > market_log_loss:
        return False
    return model_mae <= league_mae


def _merge_holdout_totals_odds(games: pd.DataFrame) -> pd.DataFrame:
    if not ODDS_2026_CSV.exists():
        return pd.DataFrame()
    odds = pd.read_csv(ODDS_2026_CSV)
    if "ou_line" not in odds.columns:
        return pd.DataFrame()
    g = games.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    g["home_team"] = g["home_team"].map(normalize_nba_team_name)
    g["away_team"] = g["away_team"].map(normalize_nba_team_name)
    o = odds.copy()
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    o["home_team"] = o["home_team"].map(normalize_nba_team_name)
    o["away_team"] = o["away_team"].map(normalize_nba_team_name)
    merged = g.merge(o, on=["date", "home_team", "away_team"], how="inner")
    valid = merged.apply(
        lambda r: pd.notna(r.get("ou_line"))
        and is_valid_american_odds(r.get("over_odds"))
        and is_valid_american_odds(r.get("under_odds")),
        axis=1,
    )
    return merged[valid].copy()


def run_training() -> dict[str, Any]:
    raw = load_games()
    raw = raw[raw["home_score"].notna() & raw["away_score"].notna()].copy()
    raw["total_points"] = raw["home_score"].astype(float) + raw["away_score"].astype(float)

    feat = build_features_for_history(raw)
    if "total_points" not in feat.columns:
        if "home_score" in feat.columns and "away_score" in feat.columns:
            feat["total_points"] = (
                feat["home_score"].astype(float) + feat["away_score"].astype(float)
            )
        else:
            feat = feat.merge(
                raw[["game_id", "total_points"]], on="game_id", how="left"
            )
    train = feat[feat["season"].isin(TRAIN_SEASONS)].copy()
    test = feat[feat["season"] == HOLDOUT_SEASON].copy()

    x_train = train[TOTALS_FEATURE_COLUMNS].values
    y_train = train["total_points"].values
    x_test = test[TOTALS_FEATURE_COLUMNS].values
    y_test = test["total_points"].values

    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(x_train, y_train)
    pred_test = reg.predict(x_test)
    residuals = y_test - pred_test
    total_std = float(np.std(residuals, ddof=1))
    if math.isnan(total_std) or total_std <= 0:
        total_std = DEFAULT_TOTAL_STD

    mae_model = float(mean_absolute_error(y_test, pred_test))
    mae_league = float(mean_absolute_error(y_test, np.full(len(y_test), LEAGUE_AVG_TOTAL)))

    merged = _merge_holdout_totals_odds(test)
    model_ll = market_ll = league_ll = hit_edge = None
    if not merged.empty:
        pred_map = dict(zip(test["game_id"].astype(str), pred_test))
        merged["expected_total_pts"] = merged["game_id"].astype(str).map(pred_map)
        merged = merged[merged["expected_total_pts"].notna()].copy()
        merged["went_over"] = merged.apply(
            lambda r: actual_went_over(
                float(r.home_score) + float(r.away_score), float(r.ou_line)
            ),
            axis=1,
        )
        merged["model_prob_over"] = [
            prob_over_normal(float(mu), total_std, float(line))
            for mu, line in zip(merged["expected_total_pts"], merged["ou_line"])
        ]
        market_probs = []
        for row in merged.itertuples(index=False):
            mo, _ = market_probs_from_american_totals(
                int(row.over_odds), int(row.under_odds)
            )
            market_probs.append(mo)
        merged["market_prob_over"] = market_probs
        merged["league_prob_over"] = [
            prob_over_normal(LEAGUE_AVG_TOTAL, total_std, float(line))
            for line in merged["ou_line"]
        ]
        y = merged["went_over"].astype(int).values
        model_ll = float(
            log_loss(y, np.clip(merged["model_prob_over"], 1e-6, 1 - 1e-6))
        )
        market_ll = float(
            log_loss(y, np.clip(merged["market_prob_over"], 1e-6, 1 - 1e-6))
        )
        league_ll = float(
            log_loss(y, np.clip(merged["league_prob_over"], 1e-6, 1 - 1e-6))
        )
        edges = merged["model_prob_over"] - merged["market_prob_over"]
        picks = merged[edges.abs() >= DEFAULT_MIN_EDGE]
        if not picks.empty:
            correct = []
            for row in picks.itertuples(index=False):
                edge = row.model_prob_over - row.market_prob_over
                if edge >= DEFAULT_MIN_EDGE:
                    correct.append(int(row.went_over) == 1)
                elif edge <= -DEFAULT_MIN_EDGE:
                    correct.append(int(row.went_over) == 0)
            hit_edge = float(sum(correct) / len(correct)) if correct else None

    gate_passes = totals_production_gate_passes(
        model_ll, market_ll, mae_model, mae_league
    )

    artifact = {
        "model": reg,
        "model_version": "v1_gbr_normal",
        "feature_columns": TOTALS_FEATURE_COLUMNS,
        "total_std": total_std,
        "league_avg_total": LEAGUE_AVG_TOTAL,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_ARTIFACT)

    manifest = {
        "track": "nba_totals",
        "model_version": artifact["model_version"],
        "path": "data/processed/nba_totals_model.joblib",
        "production_ready": gate_passes,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    ACTIVE_TOTALS_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_TOTALS_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results: dict[str, Any] = {
        "train_seasons": list(TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "holdout_with_ou_lines": len(merged) if not merged.empty else 0,
        "production_model": artifact["model_version"],
        "over_prob_method": "normal_cdf",
        "holdout_mae_total_pts": round(mae_model, 3),
        "league_avg_mae_total_pts": round(mae_league, 3),
        "holdout_total_std": round(total_std, 3),
        "log_loss_model": round(model_ll, 4) if model_ll else None,
        "log_loss_market": round(market_ll, 4) if market_ll else None,
        "log_loss_league_avg": round(league_ll, 4) if league_ll else None,
        "hit_rate_edge_flagged": hit_edge,
        "totals_production_gate_passes": gate_passes,
        "board_totals_enabled": gate_passes,
        "note": (
            "Import ou_line/over_odds/under_odds into nba_odds_2026.csv for holdout eval. "
            "Live board uses Odds API totals in same request as h2h+spreads."
        ),
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
    raise FileNotFoundError(
        f"No NBA totals model at {MODEL_ARTIFACT}. Run scripts/train_nba_totals.py first."
    )


def load_totals_manifest() -> dict[str, Any] | None:
    if not ACTIVE_TOTALS_MANIFEST.exists():
        return None
    try:
        return json.loads(ACTIVE_TOTALS_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_totals_production_ready() -> bool:
    manifest = load_totals_manifest()
    return bool(manifest and manifest.get("production_ready"))


def predict_expected_total(df: pd.DataFrame) -> np.ndarray:
    artifact = load_totals_artifact()
    cols = artifact["feature_columns"]
    prepared = build_features_for_slate(df) if "home_last10_pts_for" not in df.columns else df
    missing = [c for c in cols if c not in prepared.columns]
    if missing:
        prepared = build_features_for_slate(df)
    return artifact["model"].predict(prepared[cols].values)


def enrich_totals_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add expected_total_pts and model_prob_over when ou_line is present."""
    artifact = load_totals_artifact()
    std = float(artifact.get("total_std", DEFAULT_TOTAL_STD))
    out = df.copy()
    expected = predict_expected_total(out)
    out["expected_total_pts"] = expected
    probs: list[float | None] = []
    for exp, row in zip(expected, out.itertuples(index=False)):
        line = getattr(row, "ou_line", None)
        if line is None or (isinstance(line, float) and math.isnan(line)):
            probs.append(None)
        else:
            probs.append(round(prob_over_normal(float(exp), std, float(line)), 4))
    out["model_prob_over"] = probs
    return out
