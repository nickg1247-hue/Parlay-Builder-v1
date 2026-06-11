"""Walk-forward CFB backtest — per-season predictions vs actuals + feature effects."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import PROJECT_ROOT
from app.features.cfb_pregame import (
    FEATURE_COLUMNS,
    MARGIN_FEATURE_COLUMNS,
    TOTALS_FEATURE_COLUMNS,
    build_features_for_history,
    build_margin_features_for_history,
)
from app.models.cfb_baseline import (
    compute_metrics,
    load_games,
    predict_elo,
    predict_home_rate_constant,
    train_logistic,
)
from app.models.cfb_margin import PROXY_AWAY_SPREAD, PROXY_HOME_SPREAD
from app.models.cfb_totals import actual_went_over, prob_over_normal
from app.odds.spread_math import (
    model_prob_away_cover,
    model_prob_home_cover,
    side_covers,
)

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_backtest_report.json"

PROXY_HOME_SPREAD_BT = PROXY_HOME_SPREAD
PROXY_AWAY_SPREAD_BT = PROXY_AWAY_SPREAD


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _logistic_feature_importance(
    train: pd.DataFrame,
    feature_cols: list[str],
) -> dict[str, float]:
    """Absolute standardized logistic coefficients (larger = stronger effect)."""
    pipe = train_logistic(train, feature_cols)
    scaler: StandardScaler = pipe.named_steps["scaler"]
    clf: LogisticRegression = pipe.named_steps["clf"]
    scaled = clf.coef_[0] * scaler.scale_
    return {col: float(abs(w)) for col, w in zip(feature_cols, scaled)}


def _univariate_feature_effects(
    feat: pd.DataFrame,
    feature_cols: list[str],
) -> list[dict[str, Any]]:
    """Point-biserial |r| between each feature and home_win (exploratory)."""
    if "home_win" not in feat.columns or feat["home_win"].isna().all():
        return []
    y = feat["home_win"].astype(float)
    rows: list[dict[str, Any]] = []
    for col in feature_cols:
        if col not in feat.columns:
            continue
        x = feat[col].astype(float)
        if x.nunique() <= 1:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if np.isnan(r):
            continue
        rows.append({"feature": col, "correlation_with_home_win": round(r, 4)})
    rows.sort(key=lambda r: abs(r["correlation_with_home_win"]), reverse=True)
    return rows


def _aggregate_importance(
    fold_importances: list[dict[str, float]],
) -> list[dict[str, Any]]:
    if not fold_importances:
        return []
    keys = set()
    for d in fold_importances:
        keys.update(d.keys())
    avg: list[dict[str, Any]] = []
    for key in sorted(keys):
        vals = [d[key] for d in fold_importances if key in d]
        avg.append(
            {
                "feature": key,
                "avg_abs_logistic_coef": round(float(np.mean(vals)), 4),
                "folds": len(vals),
            }
        )
    avg.sort(key=lambda r: r["avg_abs_logistic_coef"], reverse=True)
    return avg


def _moneyline_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, float], Pipeline]:
    cols = FEATURE_COLUMNS
    y_test = test["home_win"].astype(int).values
    pipe = train_logistic(train, cols)
    probs = np.clip(pipe.predict_proba(test[cols].values)[:, 1], 1e-6, 1 - 1e-6)
    picks = (probs >= 0.5).astype(int)
    correct = int((picks == y_test).sum())
    wrong = int(len(y_test) - correct)

    model_m = compute_metrics("model", y_test, probs)
    home_rate_probs = predict_home_rate_constant(train, test)
    home_m = compute_metrics("home_rate", y_test, home_rate_probs)

    full = pd.concat([train, test], ignore_index=True).sort_values(["date", "game_id"])
    n_train = len(train)
    elo_probs = predict_elo(full)[n_train:]
    elo_m = compute_metrics("elo", y_test, elo_probs)

    naive_ll = min(home_m.log_loss, elo_m.log_loss)

    importance = _logistic_feature_importance(train, cols)

    return (
        {
            "games": len(test),
            "correct_picks": correct,
            "wrong_picks": wrong,
            "accuracy_pct": round(100.0 * correct / len(test), 2) if len(test) else 0.0,
            "log_loss": round(model_m.log_loss, 4),
            "brier": round(model_m.brier, 4),
            "beats_naive": model_m.log_loss < naive_ll,
            "naive_log_loss": round(naive_ll, 4),
            "home_rate_log_loss": round(home_m.log_loss, 4),
            "elo_log_loss": round(elo_m.log_loss, 4),
        },
        importance,
        pipe,
    )


def _spread_fold(
    train_raw: pd.DataFrame,
    test_raw: pd.DataFrame,
) -> dict[str, Any]:
    train_feat = build_margin_features_for_history(train_raw)
    test_feat = build_margin_features_for_history(
        pd.concat([train_raw, test_raw], ignore_index=True)
    )
    test_feat = test_feat[test_feat["season"] == test_raw["season"].iloc[0]].copy()
    test_feat = test_feat.merge(
        test_raw[["game_id", "home_score", "away_score"]],
        on="game_id",
        how="inner",
    )
    train_feat = train_feat.merge(
        train_raw[["game_id", "home_score", "away_score"]],
        on="game_id",
        how="inner",
    )
    train_feat["margin"] = train_feat["home_score"] - train_feat["away_score"]
    test_feat["margin"] = test_feat["home_score"] - test_feat["away_score"]

    cols = MARGIN_FEATURE_COLUMNS
    reg = GradientBoostingRegressor(
        n_estimators=120, max_depth=3, learning_rate=0.08, random_state=42
    )
    reg.fit(train_feat[cols].values, train_feat["margin"].values)
    pred = reg.predict(test_feat[cols].values)
    residuals = train_feat["margin"].values - reg.predict(train_feat[cols].values)
    margin_std = float(np.std(residuals, ddof=1))
    if np.isnan(margin_std) or margin_std <= 0:
        margin_std = 14.0

    mae = float(mean_absolute_error(test_feat["margin"], pred))

    spread_correct = 0
    pick_made = 0

    for row, margin_pred in zip(test_feat.itertuples(index=False), pred):
        hc = int(
            side_covers(
                "home",
                row.home_score,
                row.away_score,
                PROXY_HOME_SPREAD_BT,
                PROXY_AWAY_SPREAD_BT,
            )
        )
        ac = int(
            side_covers(
                "away",
                row.home_score,
                row.away_score,
                PROXY_HOME_SPREAD_BT,
                PROXY_AWAY_SPREAD_BT,
            )
        )
        p_home = model_prob_home_cover(float(margin_pred), margin_std, PROXY_HOME_SPREAD_BT)
        p_away = model_prob_away_cover(float(margin_pred), margin_std, PROXY_AWAY_SPREAD_BT)
        if p_home >= p_away:
            spread_correct += int(hc)
        else:
            spread_correct += int(ac)
        pick_made += 1

    n = len(test_feat)
    spread_acc = round(100.0 * spread_correct / pick_made, 2) if pick_made else 0.0

    return {
        "games": n,
        "proxy_spread": PROXY_HOME_SPREAD_BT,
        "margin_mae": round(mae, 3),
        "spread_picks_made": pick_made,
        "spread_pick_accuracy_pct": spread_acc,
    }


def _totals_fold(
    train_raw: pd.DataFrame,
    test_raw: pd.DataFrame,
) -> dict[str, Any]:
    train_feat = build_margin_features_for_history(train_raw)
    test_feat = build_margin_features_for_history(
        pd.concat([train_raw, test_raw], ignore_index=True)
    )
    test_feat = test_feat[test_feat["season"] == test_raw["season"].iloc[0]].copy()
    train_feat = train_feat.merge(
        train_raw[["game_id", "home_score", "away_score"]],
        on="game_id",
        how="inner",
    )
    test_feat = test_feat.merge(
        test_raw[["game_id", "home_score", "away_score"]],
        on="game_id",
        how="inner",
    )
    train_feat["total_points"] = train_feat["home_score"] + train_feat["away_score"]
    test_feat["total_points"] = test_feat["home_score"] + test_feat["away_score"]

    cols = TOTALS_FEATURE_COLUMNS
    reg = GradientBoostingRegressor(
        n_estimators=120, max_depth=3, learning_rate=0.08, random_state=42
    )
    reg.fit(train_feat[cols].values, train_feat["total_points"].values)
    pred = reg.predict(test_feat[cols].values)
    residuals = train_feat["total_points"].values - reg.predict(train_feat[cols].values)
    total_std = float(np.std(residuals, ddof=1))
    if np.isnan(total_std) or total_std <= 0:
        total_std = 14.0

    league_avg = float(train_feat["total_points"].median())
    proxy_line = round(league_avg * 2) / 2
    if proxy_line == int(proxy_line):
        proxy_line += 0.5

    mae = float(mean_absolute_error(test_feat["total_points"], pred))
    ou_correct = 0
    ou_picks = 0
    for row, exp in zip(test_feat.itertuples(index=False), pred):
        actual_total = float(row.home_score) + float(row.away_score)
        went_over = actual_went_over(actual_total, proxy_line)
        p_over = prob_over_normal(float(exp), total_std, proxy_line)
        if p_over >= 0.5:
            ou_picks += 1
            if went_over == 1:
                ou_correct += 1
        else:
            ou_picks += 1
            if went_over == 0:
                ou_correct += 1

    return {
        "games": len(test_feat),
        "proxy_ou_line": proxy_line,
        "total_mae": round(mae, 3),
        "ou_picks_made": ou_picks,
        "ou_pick_accuracy_pct": round(100.0 * ou_correct / ou_picks, 2) if ou_picks else 0.0,
    }


def run_cfb_walk_forward_backtest(
    *,
    min_train_seasons: int = 1,
    write_cache: bool = True,
) -> dict[str, Any]:
    """
    Expanding-window walk-forward: for each holdout season, train on all prior seasons,
    predict every game, score vs actual outcomes. No future leakage in features.
    """
    try:
        games = load_games()
    except FileNotFoundError as exc:
        report = {
            "generated_at": _iso_now(),
            "status": "error",
            "error": str(exc),
            "folds": [],
        }
        if write_cache:
            REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    games = games[games["home_win"].notna()].copy()
    seasons = sorted(int(s) for s in games["season"].unique())
    feat_all = build_features_for_history(games)

    folds: list[dict[str, Any]] = []
    fold_importances: list[dict[str, float]] = []

    for holdout in seasons:
        train_seasons = [s for s in seasons if s < holdout]
        if len(train_seasons) < min_train_seasons:
            continue

        train = feat_all[feat_all["season"].isin(train_seasons)].copy()
        test = feat_all[feat_all["season"] == holdout].copy()
        train_raw = games[games["season"].isin(train_seasons)].copy()
        test_raw = games[games["season"] == holdout].copy()

        if train.empty or test.empty:
            continue

        ml_metrics, importance, _ = _moneyline_fold(train, test)
        fold_importances.append(importance)
        spread_metrics = _spread_fold(train_raw, test_raw)
        totals_metrics = _totals_fold(train_raw, test_raw)

        folds.append(
            {
                "holdout_season": holdout,
                "train_seasons": train_seasons,
                "moneyline": ml_metrics,
                "spread": spread_metrics,
                "totals": totals_metrics,
            }
        )

    if not folds:
        report = {
            "generated_at": _iso_now(),
            "status": "error",
            "error": "Need at least 2 seasons for walk-forward backtest",
            "seasons_available": seasons,
            "folds": [],
        }
        if write_cache:
            REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    total_games = sum(f["moneyline"]["games"] for f in folds)
    weighted_acc = (
        sum(f["moneyline"]["accuracy_pct"] * f["moneyline"]["games"] for f in folds)
        / total_games
    )
    weighted_ll = (
        sum(f["moneyline"]["log_loss"] * f["moneyline"]["games"] for f in folds)
        / total_games
    )
    beats_naive_all = all(f["moneyline"]["beats_naive"] for f in folds)

    feature_rank = _aggregate_importance(fold_importances)
    univariate = _univariate_feature_effects(feat_all, FEATURE_COLUMNS)

    report: dict[str, Any] = {
        "generated_at": _iso_now(),
        "status": "ok",
        "method": "walk_forward_expanding_window",
        "description": (
            "Each holdout season: train on all prior seasons only; features built "
            "chronologically with no same-day leakage; compare predictions to actual results."
        ),
        "seasons_available": seasons,
        "folds": folds,
        "aggregate": {
            "holdout_games_scored": total_games,
            "moneyline_accuracy_pct": round(weighted_acc, 2),
            "moneyline_log_loss": round(weighted_ll, 4),
            "beats_naive_every_fold": beats_naive_all,
            "spread_pick_accuracy_pct": round(
                sum(
                    f["spread"]["spread_pick_accuracy_pct"] * f["spread"]["games"]
                    for f in folds
                )
                / total_games,
                2,
            ),
            "ou_pick_accuracy_pct": round(
                sum(
                    f["totals"]["ou_pick_accuracy_pct"] * f["totals"]["games"]
                    for f in folds
                )
                / total_games,
                2,
            ),
        },
        "feature_effects": {
            "logistic_importance_avg": feature_rank,
            "univariate_correlation": univariate[:15],
            "interpretation": (
                "logistic_importance_avg: larger avg_abs_logistic_coef across folds "
                "means the feature moved the moneyline model more. "
                "univariate_correlation: exploratory |r| with home_win (not causal)."
            ),
        },
        "proof_summary": {
            "verdict": "passes_walk_forward" if beats_naive_all else "mixed_vs_naive",
            "moneyline_beats_naive_all_folds": beats_naive_all,
            "aggregate_winner_accuracy": f"{round(weighted_acc, 1)}%",
            "note": (
                "Spread/totals scored at proxy lines (-7, train-median O/U) — "
                "not sportsbook closes. Add historical odds for market proof (Phase 3)."
            ),
        },
        "report_path": "data/processed/cfb_backtest_report.json",
    }

    if write_cache:
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def load_saved_cfb_backtest_report() -> dict[str, Any]:
    if not REPORT_JSON.exists():
        return {
            "status": "missing",
            "error": "No saved report. Run: python scripts/backtest_cfb_seasons.py",
        }
    try:
        return json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "error", "error": str(exc)}
