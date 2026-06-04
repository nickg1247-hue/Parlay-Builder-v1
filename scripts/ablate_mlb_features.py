"""Wave 1 feature ablation, redundancy pruning, and Platt calibration (Phase 2.7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.features.feature_selection import drop_redundant_features
from app.features.mlb_pregame import (
    FEATURE_COLUMNS_WAVE1,
    WAVE1_EXTRA_COLUMNS,
    build_features_for_history,
)
from app.models.calibration import market_log_loss_holdout
from app.models.mlb_baseline import (
    DEFAULT_REST_DAYS,
    FEATURE_COLUMNS,
    MARKET_LOG_LOSS_BENCHMARK,
    compute_metrics,
    load_games,
    prepare_features,
    production_gate_passes,
    time_split,
    train_logistic,
    _season_era_medians,
)
from app.models.platt_calibration import PlattCalibrator
from app.config import PROJECT_ROOT

ABLATION_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_ablation_results.json"

TEAM_SEASON = [
    "home_season_win_pct",
    "away_season_win_pct",
    "home_season_run_diff",
    "away_season_run_diff",
]
TEAM_LAST30 = [
    "home_last30_win_pct",
    "away_last30_win_pct",
    "home_last30_run_diff",
    "away_last30_run_diff",
]
TEAM_SPLITS = ["home_home_split_win_pct", "away_away_split_win_pct"]
TEAM_RANKS = ["home_win_pct_rank", "away_win_pct_rank"]
PITCHER_EXTRA = [
    "home_pitcher_whip",
    "away_pitcher_whip",
    "home_pitcher_ip",
    "away_pitcher_ip",
]
PARK = ["park_factor_runs"]
LAST10 = [
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_last10_run_diff",
    "away_last10_run_diff",
]


def _subset_definitions(pruned: list[str]) -> dict[str, list[str]]:
    return {
        "v1_baseline": list(FEATURE_COLUMNS),
        "wave1_full": list(FEATURE_COLUMNS_WAVE1),
        "wave1_pruned": list(pruned),
        "v1_plus_team_season": list(FEATURE_COLUMNS) + TEAM_SEASON,
        "v1_plus_team_last30": list(FEATURE_COLUMNS) + TEAM_LAST30,
        "v1_plus_pitcher_whip_ip": list(FEATURE_COLUMNS) + PITCHER_EXTRA,
        "v1_plus_park": list(FEATURE_COLUMNS) + PARK,
        "wave1_no_rank": [c for c in FEATURE_COLUMNS_WAVE1 if c not in TEAM_RANKS],
        "wave1_no_last10": [c for c in FEATURE_COLUMNS_WAVE1 if c not in LAST10],
        "wave1_team_only": list(FEATURE_COLUMNS)
        + TEAM_SEASON
        + TEAM_LAST30
        + TEAM_SPLITS
        + TEAM_RANKS,
    }


def _format_table(rows: list[dict]) -> str:
    lines = [
        "| Subset | Features | Log loss (2025) | vs v1 | vs market |",
        "|--------|----------|-----------------|-------|-----------|",
    ]
    for r in rows:
        vs_v1 = "yes" if r["log_loss"] < r["v1_baseline"] else "no"
        vs_mkt = "yes" if r.get("beats_market") else "no"
        lines.append(
            f"| {r['name']} | {r['n_features']} | {r['log_loss']:.4f} | {vs_v1} | {vs_mkt} |"
        )
    return "\n".join(lines)


def run_ablation() -> dict:
    import math

    raw = load_games()
    train_raw, test_raw = time_split(raw)
    era_medians = _season_era_medians(train_raw)
    rest_fill = float(
        pd.concat([train_raw["home_rest_days"], train_raw["away_rest_days"]])
        .dropna()
        .median()
    )
    if math.isnan(rest_fill):
        rest_fill = float(DEFAULT_REST_DAYS)

    train_v1 = prepare_features(train_raw, era_medians, rest_fill)
    test_v1 = prepare_features(test_raw, era_medians, rest_fill)
    _, v1_metrics = train_logistic(train_v1, test_v1, FEATURE_COLUMNS)

    feat = build_features_for_history(raw)
    train_w = feat[feat["season"].isin([2023, 2024])].copy()
    test_w = feat[feat["season"] == 2025].copy()
    train_2023 = feat[feat["season"] == 2023].copy()
    cal_2024 = feat[feat["season"] == 2024].copy()

    pruned, dropped, corr = drop_redundant_features(train_w, FEATURE_COLUMNS_WAVE1)
    corr_pairs = []
    if not corr.empty:
        for i, a in enumerate(pruned):
            for b in FEATURE_COLUMNS_WAVE1:
                if b in dropped and b in corr.columns and a in corr.index:
                    r = float(corr.loc[a, b])
                    if r > 0.9:
                        corr_pairs.append({"keep": a, "drop": b, "spearman": round(r, 3)})

    subsets = _subset_definitions(pruned)
    ablation_rows: list[dict] = []
    best_name = None
    best_ll = float("inf")
    best_model = None
    best_cols: list[str] = []

    market_ll = market_log_loss_holdout(raw) or MARKET_LOG_LOSS_BENCHMARK
    v1_ll = v1_metrics.log_loss

    for name, cols in subsets.items():
        model, metrics = train_logistic(
            train_w, test_w, cols, metrics_name=f"ablation_{name}"
        )
        beats_market = metrics.log_loss <= market_ll
        beats_v1 = metrics.log_loss < v1_ll
        row = {
            "name": name,
            "features": cols,
            "n_features": len(cols),
            "log_loss": metrics.log_loss,
            "brier": metrics.brier,
            "accuracy": metrics.accuracy,
            "v1_baseline": v1_ll,
            "market_baseline": market_ll,
            "beats_v1": beats_v1,
            "beats_market": beats_market,
            "passes_production_gate": production_gate_passes(
                metrics.log_loss, v1_ll, market_ll
            ),
        }
        ablation_rows.append(row)
        if metrics.log_loss < best_ll:
            best_ll = metrics.log_loss
            best_name = name
            best_model = model
            best_cols = cols

    platt_base, _ = train_logistic(train_2023, cal_2024, pruned, metrics_name="platt_base")
    raw_cal = platt_base.predict_proba(cal_2024[pruned].values)[:, 1]
    raw_test = platt_base.predict_proba(test_w[pruned].values)[:, 1]
    platt = PlattCalibrator()
    platt_ll, platt_cal_ll = platt.fit_transform_eval(
        raw_cal,
        cal_2024["home_win"].values,
        raw_test,
        test_w["home_win"].values,
    )
    platt_metrics = compute_metrics(
        "wave1_pruned_platt_2024fit", test_w["home_win"].values, platt.transform(raw_test)
    )

    pruned_model, pruned_metrics = train_logistic(
        train_w, test_w, pruned, metrics_name="wave1_pruned_logistic"
    )

    results = {
        "phase": "2.7",
        "holdout_season": 2025,
        "calibration_fit_season": 2024,
        "platt_train_season": 2023,
        "benchmarks": {
            "market_log_loss": market_ll,
            "v1_log_loss": v1_ll,
        },
        "redundancy": {
            "threshold": 0.9,
            "method": "spearman",
            "wave1_full_n": len(FEATURE_COLUMNS_WAVE1),
            "wave1_pruned_n": len(pruned),
            "dropped": dropped,
            "kept": pruned,
            "high_corr_pairs_sample": corr_pairs[:20],
        },
        "ablation": ablation_rows,
        "best_ablation_subset": best_name,
        "best_ablation_log_loss": best_ll,
        "wave1_pruned_logistic": {
            "log_loss": pruned_metrics.log_loss,
            "brier": pruned_metrics.brier,
            "accuracy": pruned_metrics.accuracy,
            "passes_production_gate": production_gate_passes(
                pruned_metrics.log_loss, v1_ll, market_ll
            ),
        },
        "platt": {
            "feature_columns": pruned,
            "calibration_log_loss_2024": platt_cal_ll,
            "holdout_log_loss_2025": platt_ll,
            "brier": platt_metrics.brier,
            "accuracy": platt_metrics.accuracy,
            "passes_production_gate": production_gate_passes(platt_ll, v1_ll, market_ll),
        },
        "production_gate": {
            "rule": "log_loss < v1 (0.6777) AND log_loss <= market (0.6770)",
            "any_ablation_passes": any(r["passes_production_gate"] for r in ablation_rows),
            "pruned_passes": production_gate_passes(
                pruned_metrics.log_loss, v1_ll, market_ll
            ),
            "platt_passes": production_gate_passes(platt_ll, v1_ll, market_ll),
        },
        "feature_columns_wave1_pruned": pruned,
    }

    ABLATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    ABLATION_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(_format_table(ablation_rows))
    print(f"\nRedundant dropped ({len(dropped)}): {', '.join(dropped) or '(none)'}")
    print(f"Pruned logistic 2025 log loss: {pruned_metrics.log_loss:.4f}")
    print(f"Platt (fit 2024, eval 2025) log loss: {platt_ll:.4f}")
    print(f"Best subset: {best_name} ({best_ll:.4f})")
    print(f"\nWrote {ABLATION_JSON}")
    return results


if __name__ == "__main__":
    run_ablation()
