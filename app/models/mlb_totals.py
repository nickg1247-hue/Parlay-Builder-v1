"""MLB game totals (Over/Under) regression + probability model."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import log_loss, mean_absolute_error

from app.config import PROJECT_ROOT
from app.data.mlb_games import load_games_with_totals
from app.features.mlb_totals_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_totals_features_for_history,
)
from app.models.mlb_baseline import _season_era_medians, time_split
from app.odds.mlb_odds_free import TOTALS_2025_CSV
from app.odds.odds_math import market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.models.constants import DEFAULT_MIN_EDGE

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "mlb_totals_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_totals_metrics.json"

LEAGUE_AVG_TOTAL = 8.8
POISSON_VARIANCE_FLOOR = 0.5
DEFAULT_PICK_MARGIN = 0.0


@dataclass
class TotalsHoldoutMetrics:
    name: str
    mae: float
    log_loss: float | None
    hit_rate_edge: float | None


def pick_margin() -> float:
    import os

    raw = os.getenv("TOTALS_PICK_MARGIN", str(DEFAULT_PICK_MARGIN)).strip()
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_PICK_MARGIN


def actual_went_over(total_runs: float, ou_line: float) -> int:
    if ou_line % 1 == 0.5:
        return int(total_runs > ou_line)
    return int(total_runs >= ou_line)


def _poisson_cdf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    total = 0.0
    term = math.exp(-lam)
    total = term
    for i in range(1, k + 1):
        term *= lam / i
        total += term
    return min(total, 1.0)


def prob_over_poisson(expected_total: float, ou_line: float) -> float:
    """P(combined runs go over the book line) under Poisson(expected_total)."""
    lam = max(expected_total, 0.1)
    if ou_line % 1 == 0.5:
        min_runs = int(ou_line) + 1
    else:
        min_runs = int(ou_line)
    return 1.0 - _poisson_cdf(min_runs - 1, lam)


def totals_production_gate_passes(
    model_log_loss: float, market_log_loss: float | None
) -> bool:
    if market_log_loss is None:
        return False
    return model_log_loss <= market_log_loss


def _merge_totals_odds(games: pd.DataFrame) -> pd.DataFrame:
    if not TOTALS_2025_CSV.exists():
        return games
    odds = pd.read_csv(TOTALS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    holdout = games.copy()
    holdout["date"] = holdout["date"].dt.strftime("%Y-%m-%d")
    holdout["home_team"] = holdout["home_team"].map(normalize_team_name)
    holdout["away_team"] = holdout["away_team"].map(normalize_team_name)
    return holdout.merge(
        odds, on=["date", "home_team", "away_team"], how="inner"
    )


def evaluate_over_under_log_loss(
    df: pd.DataFrame,
    prob_over_col: str,
    y_col: str = "went_over",
) -> float | None:
    if df.empty or y_col not in df.columns:
        return None
    probs = np.clip(df[prob_over_col].astype(float).values, 1e-6, 1 - 1e-6)
    y = df[y_col].astype(int).values
    return float(log_loss(y, probs))


def edge_flagged_hit_rate(
    df: pd.DataFrame,
    prob_col: str,
    market_col: str,
    y_col: str = "went_over",
    min_edge: float = DEFAULT_MIN_EDGE,
) -> float | None:
    if df.empty:
        return None
    edges = df[prob_col] - df[market_col]
    picks = df[edges.abs() >= min_edge].copy()
    if picks.empty:
        return None
    correct = []
    for row in picks.itertuples(index=False):
        edge = row.model_prob_over - row.market_prob_over
        if edge >= min_edge:
            correct.append(int(row.went_over) == 1)
        elif edge <= -min_edge:
            correct.append(int(row.went_over) == 0)
    return float(sum(correct) / len(correct))


def run_training() -> dict:
    raw = load_games_with_totals()
    train_raw, test_raw = time_split(raw)

    era_medians = _season_era_medians(train_raw)
    rest_fill = float(
        pd.concat([train_raw["home_rest_days"], train_raw["away_rest_days"]])
        .dropna()
        .median()
    )
    if math.isnan(rest_fill):
        rest_fill = 1.0

    feat = build_totals_features_for_history(raw)
    train = feat[feat["season"].isin([2023, 2024])].copy()
    test = feat[feat["season"] == 2025].copy()

    x_train = train[TOTALS_FEATURE_COLUMNS].values
    y_train = train["total_runs"].values
    x_test = test[TOTALS_FEATURE_COLUMNS].values
    y_test = test["total_runs"].values

    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(x_train, y_train)
    pred_test = reg.predict(x_test)
    pred_train = reg.predict(x_train)

    mae_model = float(mean_absolute_error(y_test, pred_test))
    mae_league = float(
        mean_absolute_error(y_test, np.full(len(y_test), LEAGUE_AVG_TOTAL))
    )

    merged = pd.DataFrame()
    merged_eval = _merge_totals_odds(test)
    market_ll = None
    model_ll = None
    league_ll = None
    hit_edge = None

    if not merged_eval.empty:
        merged = merged_eval.copy()
        idx = merged["game_id"].astype(str)
        pred_map = dict(zip(test["game_id"].astype(str), pred_test))
        merged["expected_total_runs"] = idx.map(pred_map)
        merged = merged[merged["expected_total_runs"].notna()].copy()
        merged["went_over"] = merged.apply(
            lambda r: actual_went_over(r.total_runs, float(r.ou_line)), axis=1
        )
        merged["model_prob_over"] = [
            prob_over_poisson(float(mu), float(line))
            for mu, line in zip(merged["expected_total_runs"], merged["ou_line"])
        ]
        market_probs = []
        for row in merged.itertuples(index=False):
            mo, _ = market_probs_from_american_totals(
                int(row.over_odds), int(row.under_odds)
            )
            market_probs.append(mo)
        merged["market_prob_over"] = market_probs
        merged["league_prob_over"] = [
            prob_over_poisson(LEAGUE_AVG_TOTAL, float(line)) for line in merged["ou_line"]
        ]

        model_ll = evaluate_over_under_log_loss(merged, "model_prob_over")
        market_ll = evaluate_over_under_log_loss(merged, "market_prob_over")
        league_ll = evaluate_over_under_log_loss(merged, "league_prob_over")
        hit_edge = edge_flagged_hit_rate(
            merged, "model_prob_over", "market_prob_over"
        )

    replace = totals_production_gate_passes(
        model_ll or 999.0, market_ll
    ) if model_ll is not None else False

    from app.models.production_pipeline import build_totals_artifact, save_totals_promotion

    totals_payload = build_totals_artifact(
        list(TOTALS_FEATURE_COLUMNS),
        raw=raw,
    )
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(totals_payload, MODEL_ARTIFACT)
    save_totals_promotion(
        "train_totals",
        totals_payload,
        feature_set="train_mlb_totals",
    )

    results: dict[str, Any] = {
        "train_seasons": [2023, 2024],
        "holdout_season": 2025,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "holdout_with_ou_lines": len(merged) if not merged.empty else 0,
        "production_model": "v1_gbr_poisson",
        "replaced_artifact": replace,
        "metrics": {
            "league_avg_baseline": {
                "mae": mae_league,
                "log_loss": league_ll,
            },
            "gbr_totals": {
                "mae": mae_model,
                "log_loss": model_ll,
                "hit_rate_edge_flagged": hit_edge,
            },
            "market_implied": {"log_loss": market_ll},
        },
        "phase_gate": {
            "rule": "model over/under log_loss <= market on 2025 matched lines",
            "passes": replace,
        },
        "benchmark_note": "Realistic O/U direction accuracy ~53-57%; no 80% gate.",
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def is_mlb_totals_production_ready() -> bool:
    """True when active totals manifest passed holdout log-loss gate."""
    from app.models.production_pipeline import get_active_model_info

    info = get_active_model_info("totals")
    return bool(info and info.get("production_ready"))


def load_totals_artifact() -> dict:
    from app.models.production_pipeline import load_active_artifact

    try:
        return load_active_artifact("totals")
    except FileNotFoundError:
        if MODEL_ARTIFACT.exists():
            return joblib.load(MODEL_ARTIFACT)
        raise FileNotFoundError(
            f"Totals model not found at {MODEL_ARTIFACT}. Run scripts/train_mlb_totals.py"
        ) from None


def predict_expected_total_runs(df: pd.DataFrame) -> np.ndarray:
    from app.features.mlb_totals_pregame import build_totals_features_for_slate

    artifact = load_totals_artifact()
    cols = artifact["feature_columns"]
    if "home_season_runs_scored_pg" not in df.columns:
        prepared = build_totals_features_for_slate(
            df,
            era_medians=artifact["era_medians"],
            rest_fill=artifact["rest_fill"],
        )
    else:
        prepared = df
    return artifact["model"].predict(prepared[cols].values)


def predict_prob_over(df: pd.DataFrame, ou_line: float | np.ndarray) -> np.ndarray:
    expected = predict_expected_total_runs(df)
    if isinstance(ou_line, (int, float)):
        return np.array([prob_over_poisson(float(mu), float(ou_line)) for mu in expected])
    lines = np.asarray(ou_line)
    return np.array(
        [prob_over_poisson(float(mu), float(line)) for mu, line in zip(expected, lines)]
    )


def score_totals_pick(
    expected_runs: float,
    ou_line: float | None,
    model_prob_over: float | None,
    market_prob_over: float | None,
    margin: float | None = None,
) -> dict[str, Any]:
    margin = pick_margin() if margin is None else margin
    pick = None
    if ou_line is not None:
        if expected_runs > float(ou_line) + margin:
            pick = "OVER"
        elif expected_runs < float(ou_line) - margin:
            pick = "UNDER"
    edge = None
    plus_ev = False
    if model_prob_over is not None and market_prob_over is not None:
        edge_over = model_prob_over - market_prob_over
        edge_under = (1 - model_prob_over) - (1 - market_prob_over)
        if edge_over >= edge_under:
            edge = edge_over
            if edge_over >= DEFAULT_MIN_EDGE:
                plus_ev = True
                if pick is None:
                    pick = "OVER"
        else:
            edge = edge_under
            if edge_under >= DEFAULT_MIN_EDGE:
                plus_ev = True
                if pick is None:
                    pick = "UNDER"
    return {
        "expected_total_runs": round(expected_runs, 2),
        "ou_line": ou_line,
        "pick": pick,
        "model_prob_over": round(model_prob_over, 4) if model_prob_over else None,
        "market_prob_over": round(market_prob_over, 4) if market_prob_over else None,
        "total_edge": round(edge, 4) if edge is not None else None,
        "plus_ev_total": plus_ev,
    }
