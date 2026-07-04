"""Walk-forward UFC backtest — per-season moneyline predictions vs actuals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.config import PROJECT_ROOT
from app.features.ufc_pregame import FEATURE_COLUMNS, build_features_for_history
from app.models.ufc_baseline import (
    compute_metrics,
    load_fights,
    predict_elo,
    train_logistic,
)

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "ufc_backtest_report.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _logistic_feature_importance(
    train: pd.DataFrame,
) -> dict[str, float]:
    from sklearn.preprocessing import StandardScaler

    pipe = train_logistic(train)
    scaler: StandardScaler = pipe.named_steps["scaler"]
    clf: LogisticRegression = pipe.named_steps["clf"]
    scaled = clf.coef_[0] * scaler.scale_
    return {col: float(abs(w)) for col, w in zip(FEATURE_COLUMNS, scaled)}


def _univariate_feature_effects(feat: pd.DataFrame) -> list[dict[str, Any]]:
    if "home_win" not in feat.columns or feat["home_win"].isna().all():
        return []
    y = feat["home_win"].astype(float)
    rows: list[dict[str, Any]] = []
    for col in FEATURE_COLUMNS:
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


def _home_rate_probs(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    rate = float(train["home_win"].mean())
    return np.full(len(test), rate)


def _moneyline_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, float]]:
    y_test = test["home_win"].astype(int).values
    pipe = train_logistic(train)
    probs = np.clip(pipe.predict_proba(test[FEATURE_COLUMNS].values)[:, 1], 1e-6, 1 - 1e-6)
    picks = (probs >= 0.5).astype(int)
    correct = int((picks == y_test).sum())

    model_m = compute_metrics("model", y_test, probs)
    home_rate_probs = _home_rate_probs(train, test)
    home_m = compute_metrics("home_rate", y_test, home_rate_probs)

    full = pd.concat([train, test], ignore_index=True).sort_values(["date", "fight_id"])
    n_train = len(train)
    elo_probs = predict_elo(full)[n_train:]
    elo_m = compute_metrics("elo", y_test, elo_probs)
    naive_ll = min(home_m.log_loss, elo_m.log_loss)

    return (
        {
            "fights": len(test),
            "correct_picks": correct,
            "wrong_picks": int(len(y_test) - correct),
            "accuracy_pct": round(100.0 * correct / len(test), 2) if len(test) else 0.0,
            "log_loss": round(model_m.log_loss, 4),
            "brier": round(model_m.brier, 4),
            "beats_naive": model_m.log_loss < naive_ll,
            "naive_log_loss": round(naive_ll, 4),
            "home_rate_log_loss": round(home_m.log_loss, 4),
            "elo_log_loss": round(elo_m.log_loss, 4),
        },
        _logistic_feature_importance(train),
    )


def run_ufc_walk_forward_backtest(
    *,
    min_train_seasons: int = 1,
    write_cache: bool = True,
) -> dict[str, Any]:
    """Expanding-window walk-forward by calendar season."""
    try:
        fights = load_fights()
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

    fights = fights[fights["home_win"].notna()].copy()
    seasons = sorted(int(s) for s in fights["season"].unique())
    feat_all = build_features_for_history(fights)

    folds: list[dict[str, Any]] = []
    fold_importances: list[dict[str, float]] = []

    for holdout in seasons:
        train_seasons = [s for s in seasons if s < holdout]
        if len(train_seasons) < min_train_seasons:
            continue
        train = feat_all[feat_all["season"].isin(train_seasons)].copy()
        test = feat_all[feat_all["season"] == holdout].copy()
        if train.empty or test.empty:
            continue
        ml_metrics, importance = _moneyline_fold(train, test)
        fold_importances.append(importance)
        folds.append(
            {
                "holdout_season": holdout,
                "train_seasons": train_seasons,
                "moneyline": ml_metrics,
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

    total_fights = sum(f["moneyline"]["fights"] for f in folds)
    weighted_acc = (
        sum(f["moneyline"]["accuracy_pct"] * f["moneyline"]["fights"] for f in folds)
        / total_fights
    )
    weighted_ll = (
        sum(f["moneyline"]["log_loss"] * f["moneyline"]["fights"] for f in folds)
        / total_fights
    )
    beats_naive_all = all(f["moneyline"]["beats_naive"] for f in folds)

    report: dict[str, Any] = {
        "generated_at": _iso_now(),
        "status": "ok",
        "method": "walk_forward_expanding_window",
        "description": (
            "Each holdout season: train on all prior seasons only; fighter features "
            "built chronologically with no same-day leakage."
        ),
        "seasons_available": seasons,
        "folds": folds,
        "aggregate": {
            "holdout_fights_scored": total_fights,
            "moneyline_accuracy_pct": round(weighted_acc, 2),
            "moneyline_log_loss": round(weighted_ll, 4),
            "beats_naive_every_fold": beats_naive_all,
        },
        "feature_effects": {
            "logistic_importance_avg": _aggregate_importance(fold_importances),
            "univariate_correlation": _univariate_feature_effects(feat_all)[:15],
        },
        "proof_summary": {
            "verdict": "passes_walk_forward" if beats_naive_all else "mixed_vs_naive",
            "moneyline_beats_naive_all_folds": beats_naive_all,
            "aggregate_winner_accuracy": f"{round(weighted_acc, 1)}%",
            "note": "Moneyline only — no market odds in backtest (Phase 3).",
        },
        "report_path": "data/processed/ufc_backtest_report.json",
    }

    if write_cache:
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def load_saved_ufc_backtest_report() -> dict[str, Any]:
    if not REPORT_JSON.exists():
        return {
            "status": "missing",
            "error": "No saved report. Run: python scripts/evaluate_ufc.py",
        }
    try:
        return json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "error", "error": str(exc)}
