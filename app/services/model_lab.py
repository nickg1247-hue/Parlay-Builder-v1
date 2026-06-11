"""MLB Model Lab — walk-forward validation experiments with locked 2025 test."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.features.feature_selection import drop_redundant_features
from app.features.mlb_pregame import (
    BULLPEN_COLUMNS,
    FEATURE_COLUMNS_WAVE1,
    PITCHER_L5_COLUMNS,
    build_features,
    build_features_for_history,
)
from app.features.mlb_totals_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_totals_features,
)
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.mlb_baseline import (
    DEFAULT_REST_DAYS,
    FEATURE_COLUMNS,
    _season_era_medians,
    load_games,
    prepare_features,
    production_gate_passes,
    train_logistic,
)
from app.models.mlb_totals import (
    actual_went_over,
    edge_flagged_hit_rate,
    prob_over_poisson,
    totals_production_gate_passes,
)
from app.models.production_pipeline import (
    build_moneyline_platt_artifact,
    build_totals_artifact,
    save_moneyline_promotion,
    save_totals_promotion,
)
from sklearn.ensemble import GradientBoostingRegressor
from app.odds.market_eval import _merge_games_odds
from app.odds.mlb_odds_free import (
    ODDS_2025_CSV,
    load_totals_odds_for_season,
    totals_odds_csv_path,
)
from app.odds.odds_math import market_probs_from_american, market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds
from app.services.backtest_report import (
    _empty_report,
    _merge_totals_odds,
    _pick_moneyline_side,
)

LAB_DIR = PROJECT_ROOT / "data" / "processed" / "mlb_lab"
RUNS_DIR = LAB_DIR / "runs"
INDEX_JSON = LAB_DIR / "index.json"

TRAIN_MAX_SEASON = 2023
VAL_SEASON = 2024
TEST_SEASON = 2025

SPLIT_BANNER = {
    "train": f"seasons ≤ {TRAIN_MAX_SEASON}",
    "validation": f"season {VAL_SEASON} (walk-forward, tuning allowed)",
    "locked_test": f"season {TEST_SEASON} (confirm only — no tuning)",
}

TRACKS = frozenset({"moneyline", "totals"})

DEFAULT_GOAL_TOLERANCE_PCT = 0.05
MAX_QUEUE_ATTEMPTS = 20

LOWER_IS_BETTER_METRICS = frozenset(
    {
        "log_loss_model",
        "totals_log_loss_model",
        "total_runs_mae",
    }
)
HIGHER_IS_BETTER_METRICS = frozenset(
    {
        "winner_accuracy_pct",
        "ou_pick_accuracy_pct",
    }
)
BOOLEAN_GOAL_METRICS = frozenset(
    {
        "model_beats_market",
        "totals_beats_market",
    }
)

MONEYLINE_GOAL_METRICS = frozenset(
    {
        "log_loss_model",
        "winner_accuracy_pct",
        "model_beats_market",
    }
)

TOTALS_GOAL_METRICS = frozenset(
    {
        "totals_log_loss_model",
        "ou_pick_accuracy_pct",
        "total_runs_mae",
        "totals_beats_market",
    }
)

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


def _ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _read_index() -> list[dict[str, Any]]:
    _ensure_dirs()
    if not INDEX_JSON.exists():
        return []
    return json.loads(INDEX_JSON.read_text(encoding="utf-8"))


def _write_index(entries: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    INDEX_JSON.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _save_run(run: dict[str, Any]) -> None:
    _ensure_dirs()
    path = RUNS_DIR / f"{run['id']}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    entries = _read_index()
    track = run.get("track", "moneyline")
    val = run.get("validation_summary", {})
    confirm = run.get("test_confirm") or {}
    gate = confirm.get("production_gate", {})
    summary = {
        "id": run["id"],
        "track": track,
        "experiment_id": run["experiment_id"],
        "feature_set": run["feature_set"],
        "goal_metric": run["goal_metric"],
        "goal_value": run["goal_value"],
        "goal_met": run.get("goal_met"),
        "goal_within_tolerance": run.get("goal_within_tolerance"),
        "goal_gap_pct": run.get("goal_gap_pct"),
        "status": run["status"],
        "created_at": run["created_at"],
        "validation_log_loss_model": val.get("moneyline", {}).get("log_loss_model"),
        "validation_totals_log_loss": val.get("totals", {}).get("log_loss_model"),
        "totals_goal_met": run.get("goal_met") if track == "totals" else None,
        "confirmed": confirm != {},
        "gate_passed": gate.get("production_gate_passed")
        if track == "moneyline"
        else gate.get("totals_gate_passed"),
        "totals_gate_passed": gate.get("totals_gate_passed"),
    }
    entries = [e for e in entries if e["id"] != run["id"]]
    entries.insert(0, summary)
    _write_index(entries)


def list_runs() -> list[dict[str, Any]]:
    return _read_index()


def get_run(run_id: str) -> dict[str, Any] | None:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def feature_set_registry(train_wave: pd.DataFrame) -> dict[str, list[str]]:
    pruned, _, _ = drop_redundant_features(train_wave, FEATURE_COLUMNS_WAVE1)
    tier1 = list(FEATURE_COLUMNS_WAVE1) + PITCHER_L5_COLUMNS + BULLPEN_COLUMNS
    pruned_l5, _, _ = drop_redundant_features(
        train_wave, list(pruned) + PITCHER_L5_COLUMNS
    )
    pruned_bullpen, _, _ = drop_redundant_features(
        train_wave, list(pruned) + BULLPEN_COLUMNS
    )
    pruned_l5_bullpen, _, _ = drop_redundant_features(train_wave, tier1)
    return {
        "v1_baseline": list(FEATURE_COLUMNS),
        "wave1_full": list(FEATURE_COLUMNS_WAVE1),
        "wave1_pruned": list(pruned),
        "wave1_pruned_pitcher_l5": list(pruned_l5),
        "wave1_pruned_bullpen": list(pruned_bullpen),
        "wave1_pruned_l5_bullpen": list(pruned_l5_bullpen),
        "v1_plus_team_season": list(FEATURE_COLUMNS) + TEAM_SEASON,
        "v1_plus_team_last30": list(FEATURE_COLUMNS) + TEAM_LAST30,
        "v1_plus_pitcher_whip_ip": list(FEATURE_COLUMNS) + PITCHER_EXTRA,
        "v1_plus_park": list(FEATURE_COLUMNS) + PARK,
        "wave1_no_rank": [c for c in FEATURE_COLUMNS_WAVE1 if c not in TEAM_RANKS],
        "wave1_no_last10": [c for c in FEATURE_COLUMNS_WAVE1 if c not in LAST10],
    }


def list_moneyline_feature_sets() -> list[str]:
    raw = load_games()
    feat = build_features_for_history(raw)
    train_wave = feat[feat["season"] <= TRAIN_MAX_SEASON]
    return sorted(feature_set_registry(train_wave).keys())


def totals_feature_set_registry() -> dict[str, list[str]]:
    cols = list(TOTALS_FEATURE_COLUMNS)
    last30 = [c for c in cols if "last30" in c]
    last10 = [c for c in cols if "last10" in c]
    splits = [c for c in cols if "split" in c]
    h2h = ["h2h_avg_total_runs"]
    season = [c for c in cols if "season_runs" in c]
    pitcher = [c for c in cols if "pitcher" in c]
    core = ["park_factor_runs", "home_rest_days", "away_rest_days"]
    return {
        "totals_full": cols,
        "totals_no_h2h": [c for c in cols if c not in h2h],
        "totals_no_last30": [c for c in cols if c not in last30],
        "totals_no_last10": [c for c in cols if c not in last10],
        "totals_no_splits": [c for c in cols if c not in splits],
        "totals_season_pitcher": season + pitcher + core,
    }


def list_totals_feature_sets() -> list[str]:
    return sorted(totals_feature_set_registry().keys())


def get_lab_meta() -> dict[str, Any]:
    return {
        "tracks": ["moneyline", "totals"],
        "splits": SPLIT_BANNER,
        "default_until_within_pct": DEFAULT_GOAL_TOLERANCE_PCT,
        "moneyline": {
            "feature_sets": list_moneyline_feature_sets(),
            "goal_metrics": sorted(MONEYLINE_GOAL_METRICS),
        },
        "totals": {
            "feature_sets": list_totals_feature_sets(),
            "goal_metrics": sorted(TOTALS_GOAL_METRICS),
        },
    }


def _feature_sets_for_track(track: str) -> list[str]:
    if track == "moneyline":
        return list_moneyline_feature_sets()
    return list_totals_feature_sets()


def _assert_valid_feature_set(track: str, feature_set: str) -> None:
    all_sets = _feature_sets_for_track(track)
    if feature_set not in all_sets:
        raise ValueError(
            f"Unknown {track} feature_set '{feature_set}'. "
            f"Options: {sorted(all_sets)}"
        )


def _feature_set_queue(track: str, start_feature_set: str) -> list[str]:
    all_sets = _feature_sets_for_track(track)
    return [start_feature_set] + [s for s in all_sets if s != start_feature_set]


def goal_gap_pct(metric: str, actual: float, goal: float) -> float | None:
    """Relative distance from goal (0 = at or better than goal)."""
    if metric in BOOLEAN_GOAL_METRICS:
        return 0.0 if bool(actual) == bool(goal) else 1.0
    if goal == 0:
        return abs(float(actual) - goal)
    if metric in LOWER_IS_BETTER_METRICS:
        if float(actual) <= goal:
            return 0.0
        return (float(actual) - goal) / abs(goal)
    if metric in HIGHER_IS_BETTER_METRICS:
        if float(actual) >= goal:
            return 0.0
        return (goal - float(actual)) / abs(goal)
    return None


def goal_within_tolerance(
    metric: str,
    actual: Any,
    goal: float,
    tolerance_pct: float = DEFAULT_GOAL_TOLERANCE_PCT,
) -> bool:
    if actual is None:
        return False
    if metric in BOOLEAN_GOAL_METRICS:
        return bool(actual) == bool(goal)
    gap = goal_gap_pct(metric, float(actual), goal)
    if gap is None:
        return False
    return gap <= tolerance_pct


def _run_leakage_preflight() -> dict[str, Any]:
    """Mirror tests/test_no_leakage.py assertions."""
    games = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "date": "2025-06-01",
                "home_team": "TeamA",
                "away_team": "TeamB",
                "season": 2025,
                "home_score": 5,
                "away_score": 2,
                "home_win": 1,
            },
            {
                "game_id": "g2",
                "date": "2025-06-02",
                "home_team": "TeamA",
                "away_team": "TeamC",
                "season": 2025,
                "home_score": 1,
                "away_score": 4,
                "home_win": 0,
            },
            {
                "game_id": "g3",
                "date": "2025-06-03",
                "home_team": "TeamA",
                "away_team": "TeamD",
                "season": 2025,
                "home_score": 3,
                "away_score": 3,
                "home_win": 0,
            },
        ]
    )
    feats = build_features(games)
    row_g2 = feats[feats["game_id"] == "g2"].iloc[0]
    assert row_g2["home_season_win_pct"] == 1.0
    assert row_g2["home_season_run_diff"] == 3.0
    row_g3 = feats[feats["game_id"] == "g3"].iloc[0]
    assert row_g3["home_season_win_pct"] == 0.5
    assert row_g3["home_season_run_diff"] == 0.0
    return {"passed": True, "checks": ["future_games_excluded"]}


def _shuffle_label_sanity(train_df: pd.DataFrame, feature_cols: list[str]) -> dict[str, Any]:
    """Shuffled labels should not produce strong accuracy (leakage smell test)."""
    shuffled = train_df.copy()
    rng = np.random.default_rng(42)
    shuffled["home_win"] = rng.permutation(shuffled["home_win"].values)
    model, metrics = train_logistic(
        shuffled,
        shuffled,
        feature_cols,
        metrics_name="shuffle_sanity",
    )
    passed = metrics.accuracy <= 0.58 and metrics.log_loss >= 0.65
    return {
        "passed": passed,
        "accuracy": round(metrics.accuracy, 4),
        "log_loss": round(metrics.log_loss, 4),
        "threshold_accuracy_max": 0.58,
        "threshold_log_loss_min": 0.65,
    }


def _run_totals_leakage_preflight() -> dict[str, Any]:
    """Mirror tests/test_totals_no_leakage.py assertions."""
    games = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-06-01",
                "home_team": "A",
                "away_team": "B",
                "season": 2025,
                "home_score": 5,
                "away_score": 3,
                "total_runs": 8,
            },
            {
                "game_id": "2",
                "date": "2025-06-02",
                "home_team": "A",
                "away_team": "C",
                "season": 2025,
                "home_score": 2,
                "away_score": 1,
                "total_runs": 3,
            },
            {
                "game_id": "3",
                "date": "2025-06-03",
                "home_team": "A",
                "away_team": "D",
                "season": 2025,
                "home_score": 4,
                "away_score": 4,
                "total_runs": 8,
            },
        ]
    )
    feats = build_totals_features(games)
    row2 = feats[feats["game_id"] == "2"].iloc[0]
    assert row2["home_season_runs_scored_pg"] == 5.0
    row3 = feats[feats["game_id"] == "3"].iloc[0]
    assert row3["home_season_runs_scored_pg"] == 3.5
    return {"passed": True, "checks": ["totals_rolling_excludes_current"]}


def _shuffle_totals_label_sanity(
    train_df: pd.DataFrame, feature_cols: list[str]
) -> dict[str, Any]:
    """Shuffled went_over should not look predictive."""
    df = train_df.copy()
    df["went_over"] = (df["total_runs"] >= 8.5).astype(int)
    rng = np.random.default_rng(42)
    df["went_over"] = rng.permutation(df["went_over"].values)
    x = df[feature_cols].values
    y = df["went_over"].values
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )
    model.fit(x, y)
    probs = model.predict_proba(x)[:, 1]
    acc = float(((probs >= 0.5) == y).mean())
    ll = float(log_loss(y, np.clip(probs, 1e-6, 1 - 1e-6)))
    passed = acc <= 0.58 and ll >= 0.65
    return {
        "passed": passed,
        "accuracy": round(acc, 4),
        "log_loss": round(ll, 4),
        "threshold_accuracy_max": 0.58,
        "threshold_log_loss_min": 0.65,
    }


def run_preflight_moneyline(
    feature_cols: list[str], train_df: pd.DataFrame
) -> dict[str, Any]:
    leakage = _run_leakage_preflight()
    shuffle = _shuffle_label_sanity(train_df, feature_cols)
    return {
        "track": "moneyline",
        "leakage": leakage,
        "shuffle_labels": shuffle,
        "passed": leakage["passed"] and shuffle["passed"],
    }


def run_preflight_totals(
    feature_cols: list[str], train_df: pd.DataFrame
) -> dict[str, Any]:
    leakage = _run_totals_leakage_preflight()
    shuffle = _shuffle_totals_label_sanity(train_df, feature_cols)
    return {
        "track": "totals",
        "leakage": leakage,
        "shuffle_labels": shuffle,
        "passed": leakage["passed"] and shuffle["passed"],
    }


def _fit_totals_regressor(
    train_df: pd.DataFrame, feature_cols: list[str]
) -> GradientBoostingRegressor:
    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(train_df[feature_cols].values, train_df["total_runs"].values)
    return reg


def _window_season(raw_window: pd.DataFrame) -> int | None:
    if raw_window.empty:
        return None
    return int(raw_window["season"].iloc[0])


def _score_window_ml(
    model,
    feature_cols: list[str],
    win_feat: pd.DataFrame,
    raw_window: pd.DataFrame,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> dict[str, Any]:
    if win_feat.empty:
        return _empty_report(0)["moneyline"]
    missing = [c for c in feature_cols if c not in win_feat.columns]
    if missing:
        raise ValueError(f"Feature columns missing from frame: {missing}")

    probs = model.predict_proba(win_feat[feature_cols].values)[:, 1]
    y_true = win_feat["home_win"].astype(int).values
    model_ll = float(log_loss(y_true, np.clip(probs, 1e-6, 1 - 1e-6)))
    model_pick = probs >= 0.5
    winner_acc = float((model_pick == (y_true == 1)).mean() * 100)

    block = {
        "games_with_odds": len(win_feat),
        "winner_accuracy_pct": round(winner_acc, 2),
        "plus_ev_picks": 0,
        "plus_ev_accuracy_pct": 0.0,
        "log_loss_model": round(model_ll, 4),
        "log_loss_market": None,
        "model_beats_market": None,
        "min_edge": min_edge,
    }

    if (
        ODDS_2025_CSV.exists()
        and not raw_window.empty
        and int(raw_window["season"].iloc[0]) == TEST_SEASON
    ):
        odds = pd.read_csv(ODDS_2025_CSV)
        matched = _merge_games_odds(raw_window, odds)
        valid = matched.apply(
            lambda r: is_valid_american_odds(r.home_ml)
            and is_valid_american_odds(r.away_ml),
            axis=1,
        )
        matched = matched[valid].copy()
        if not matched.empty:
            feat_slice = win_feat.copy()
            feat_slice["game_id"] = feat_slice["game_id"].astype(str)
            matched = matched.copy()
            matched["game_id"] = matched["game_id"].astype(str)
            merge_cols = ["game_id"] + feature_cols + ["home_win"]
            scored = feat_slice[merge_cols].merge(
                matched[["game_id", "home_ml", "away_ml"]],
                on="game_id",
                how="inner",
            )
            probs_m = model.predict_proba(scored[feature_cols].values)[:, 1]
            scored = scored.copy()
            scored["model_prob_home"] = probs_m
            scored["model_prob_away"] = 1.0 - probs_m
            market_home = []
            market_away = []
            for row in scored.itertuples(index=False):
                mh, ma = market_probs_from_american(int(row.home_ml), int(row.away_ml))
                market_home.append(mh)
                market_away.append(ma)
            scored["market_prob_home"] = market_home
            scored["market_prob_away"] = market_away
            scored["edge_home"] = scored["model_prob_home"] - scored["market_prob_home"]
            scored["edge_away"] = scored["model_prob_away"] - scored["market_prob_away"]
            y = scored["home_win"].astype(int).values
            market_ll = float(
                log_loss(y, np.clip(scored["market_prob_home"], 1e-6, 1 - 1e-6))
            )
            model_ll_o = float(
                log_loss(y, np.clip(scored["model_prob_home"], 1e-6, 1 - 1e-6))
            )
            scored["pick_side"] = scored.apply(
                lambda r: _pick_moneyline_side(r, min_edge), axis=1
            )
            plus_ev = scored[scored["pick_side"].notna()]
            pick_wins = []
            for row in plus_ev.itertuples(index=False):
                if row.pick_side == "home":
                    pick_wins.append(int(row.home_win) == 1)
                else:
                    pick_wins.append(int(row.home_win) == 0)
            block.update(
                {
                    "games_with_odds": len(scored),
                    "log_loss_model": round(model_ll_o, 4),
                    "log_loss_market": round(market_ll, 4),
                    "model_beats_market": model_ll_o < market_ll,
                    "plus_ev_picks": len(plus_ev),
                    "plus_ev_accuracy_pct": round(
                        float(np.mean(pick_wins) * 100) if pick_wins else 0.0, 2
                    ),
                }
            )

    return block


def _score_window_totals(
    reg,
    eval_df: pd.DataFrame,
    raw_window: pd.DataFrame,
    feature_cols: list[str],
    min_edge: float = DEFAULT_MIN_EDGE,
) -> dict[str, Any]:
    if eval_df.empty:
        return _empty_report(0)["totals"]

    pred = reg.predict(eval_df[feature_cols].values)
    actual = eval_df["total_runs"].astype(float)
    mae = float(np.mean(np.abs(pred - actual)))
    bias = float(np.mean(pred - actual))

    block = {
        "games_with_ou_line": len(eval_df),
        "ou_pick_accuracy_pct": 0.0,
        "plus_ev_ou_picks": 0,
        "plus_ev_ou_accuracy_pct": 0.0,
        "total_runs_mae": round(mae, 3),
        "total_runs_bias": round(bias, 3),
        "log_loss_model": None,
        "log_loss_market": None,
        "min_edge": min_edge,
    }

    season = _window_season(raw_window)
    odds = load_totals_odds_for_season(season) if season in (VAL_SEASON, TEST_SEASON) else None
    if odds is not None and not odds.empty:
        merged = _merge_totals_odds(eval_df, odds)
        valid = merged.apply(
            lambda r: is_valid_american_odds(r.over_odds)
            and is_valid_american_odds(r.under_odds),
            axis=1,
        )
        merged = merged[valid].copy()
        if not merged.empty:
            merged["expected_total_runs"] = reg.predict(merged[feature_cols].values)
            merged["model_prob_over"] = [
                prob_over_poisson(float(mu), float(line))
                for mu, line in zip(merged["expected_total_runs"], merged["ou_line"])
            ]
            market_o = []
            for row in merged.itertuples(index=False):
                mo, _ = market_probs_from_american_totals(
                    int(row.over_odds), int(row.under_odds)
                )
                market_o.append(mo)
            merged["market_prob_over"] = market_o
            merged["went_over"] = merged.apply(
                lambda r: actual_went_over(r.total_runs, float(r.ou_line)), axis=1
            )
            y = merged["went_over"].astype(int).values
            model_ll = float(
                log_loss(
                    y,
                    np.clip(merged["model_prob_over"], 1e-6, 1 - 1e-6),
                )
            )
            market_ll = float(
                log_loss(
                    y,
                    np.clip(merged["market_prob_over"], 1e-6, 1 - 1e-6),
                )
            )
            model_pick_over = merged["model_prob_over"] >= 0.5
            ou_acc = float(
                (model_pick_over == (merged["went_over"] == 1)).mean() * 100
            )
            edges = merged["model_prob_over"] - merged["market_prob_over"]
            plus_ev_n = int((edges.abs() >= min_edge).sum())
            hit = edge_flagged_hit_rate(
                merged, "model_prob_over", "market_prob_over", min_edge=min_edge
            )
            block.update(
                {
                    "games_with_ou_line": len(merged),
                    "ou_pick_accuracy_pct": round(ou_acc, 2),
                    "plus_ev_ou_picks": plus_ev_n,
                    "plus_ev_ou_accuracy_pct": round(
                        float(hit * 100) if hit is not None else 0.0, 2
                    ),
                    "log_loss_model": round(model_ll, 4),
                    "log_loss_market": round(market_ll, 4),
                    "totals_beats_market": model_ll <= market_ll,
                }
            )

    return block


def _walk_forward_validation_moneyline(
    raw: pd.DataFrame,
    feature_cols: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Monthly learning curve on 2024; model trained only on seasons ≤ 2023."""
    val_raw = raw[raw["season"] == VAL_SEASON].copy()
    if val_raw.empty:
        raise ValueError(f"No validation games for season {VAL_SEASON}")

    feat_all = build_features_for_history(raw)
    tot_all = build_totals_features(raw, update_state=True)

    train_feat = feat_all[feat_all["season"] <= TRAIN_MAX_SEASON].copy()
    val_feat = feat_all[feat_all["season"] == VAL_SEASON].copy()
    model, _ = train_logistic(
        train_feat,
        val_feat,
        feature_cols,
        metrics_name="lab_val_full",
    )

    learning_curve: list[dict[str, Any]] = []
    for period in sorted(val_raw["date"].dt.to_period("M").unique()):
        month_start = period.to_timestamp()
        month_end = (period + 1).to_timestamp()
        month_raw = val_raw[
            (val_raw["date"] >= month_start) & (val_raw["date"] < month_end)
        ]
        month_feat = val_feat[val_feat["game_id"].isin(month_raw["game_id"])]
        if month_feat.empty:
            continue
        ml_block = _score_window_ml(model, feature_cols, month_feat, month_raw)
        learning_curve.append(
            {
                "month": str(period),
                "games": len(month_feat),
                "log_loss_model": ml_block["log_loss_model"],
                "winner_accuracy_pct": ml_block["winner_accuracy_pct"],
            }
        )

    ml_summary = _score_window_ml(model, feature_cols, val_feat, val_raw)

    tot_train = tot_all[tot_all["season"] <= TRAIN_MAX_SEASON]
    tot_val = tot_all[tot_all["season"] == VAL_SEASON]
    totals_reg = _fit_totals_regressor(tot_train, list(TOTALS_FEATURE_COLUMNS))
    tot_summary = _score_window_totals(
        totals_reg, tot_val, val_raw, list(TOTALS_FEATURE_COLUMNS)
    )

    return learning_curve, ml_summary, tot_summary


def _walk_forward_validation_totals(
    raw: pd.DataFrame,
    feature_cols: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Monthly O/U log loss + MAE on 2024; GBR trained on seasons ≤ 2023."""
    val_raw = raw[raw["season"] == VAL_SEASON].copy()
    if val_raw.empty:
        raise ValueError(f"No validation games for season {VAL_SEASON}")

    tot_all = build_totals_features(raw, update_state=True)
    train_tot = tot_all[tot_all["season"] <= TRAIN_MAX_SEASON]
    val_tot = tot_all[tot_all["season"] == VAL_SEASON]
    reg = _fit_totals_regressor(train_tot, feature_cols)

    learning_curve: list[dict[str, Any]] = []
    for period in sorted(val_raw["date"].dt.to_period("M").unique()):
        month_start = period.to_timestamp()
        month_end = (period + 1).to_timestamp()
        month_raw = val_raw[
            (val_raw["date"] >= month_start) & (val_raw["date"] < month_end)
        ]
        month_tot = val_tot[val_tot["game_id"].isin(month_raw["game_id"])]
        if month_tot.empty:
            continue
        block = _score_window_totals(reg, month_tot, month_raw, feature_cols)
        learning_curve.append(
            {
                "month": str(period),
                "games": len(month_tot),
                "log_loss_model": block.get("log_loss_model"),
                "total_runs_mae": block.get("total_runs_mae"),
            }
        )

    tot_summary = _score_window_totals(reg, val_tot, val_raw, feature_cols)

    feat_all = build_features_for_history(raw)
    train_feat = feat_all[feat_all["season"] <= TRAIN_MAX_SEASON]
    val_feat = feat_all[feat_all["season"] == VAL_SEASON]
    ml_model, _ = train_logistic(
        train_feat, val_feat, list(FEATURE_COLUMNS), metrics_name="lab_info_ml"
    )
    ml_summary = _score_window_ml(ml_model, list(FEATURE_COLUMNS), val_feat, val_raw)

    return learning_curve, tot_summary, ml_summary


def _goal_met(track: str, metric: str, value: Any, goal_value: float) -> bool:
    if track == "moneyline":
        if metric == "log_loss_model":
            return float(value) <= goal_value
        if metric == "winner_accuracy_pct":
            return float(value) >= goal_value
        if metric == "model_beats_market":
            return bool(value) == bool(goal_value)
    if track == "totals":
        if metric == "totals_log_loss_model":
            return float(value) <= goal_value
        if metric == "ou_pick_accuracy_pct":
            return float(value) >= goal_value
        if metric == "total_runs_mae":
            return float(value) <= goal_value
        if metric == "totals_beats_market":
            return bool(value) == bool(goal_value)
    return False


def _metric_value_for_goal(track: str, metric: str, summary: dict[str, Any]) -> Any:
    if track == "moneyline":
        return summary.get("moneyline", {}).get(metric)
    key = metric
    if metric == "totals_log_loss_model":
        key = "log_loss_model"
    if metric == "totals_beats_market":
        key = "totals_beats_market"
    return summary.get("totals", {}).get(key)


def run_experiment(
    experiment_id: str,
    track: str,
    feature_set: str,
    goal_metric: str,
    goal_value: float,
    *,
    tolerance_pct: float = DEFAULT_GOAL_TOLERANCE_PCT,
) -> dict[str, Any]:
    if track not in TRACKS:
        raise ValueError(f"track must be one of {sorted(TRACKS)}")

    raw = load_games()

    if track == "moneyline":
        if goal_metric not in MONEYLINE_GOAL_METRICS:
            raise ValueError(
                f"goal_metric must be one of {sorted(MONEYLINE_GOAL_METRICS)}"
            )
        feat_all = build_features_for_history(raw)
        registry = feature_set_registry(feat_all[feat_all["season"] <= TRAIN_MAX_SEASON])
        if feature_set not in registry:
            raise ValueError(
                f"Unknown moneyline feature_set '{feature_set}'. "
                f"Options: {sorted(registry)}"
            )
        feature_cols = registry[feature_set]
        preflight = run_preflight_moneyline(
            feature_cols, feat_all[feat_all["season"] <= TRAIN_MAX_SEASON]
        )
    else:
        if goal_metric not in TOTALS_GOAL_METRICS:
            raise ValueError(
                f"goal_metric must be one of {sorted(TOTALS_GOAL_METRICS)}"
            )
        registry = totals_feature_set_registry()
        if feature_set not in registry:
            raise ValueError(
                f"Unknown totals feature_set '{feature_set}'. "
                f"Options: {sorted(registry)}"
            )
        feature_cols = registry[feature_set]
        tot_all = build_totals_features(raw, update_state=True)
        preflight = run_preflight_totals(
            feature_cols, tot_all[tot_all["season"] <= TRAIN_MAX_SEASON]
        )

    if not preflight["passed"]:
        run_id = str(uuid.uuid4())
        run = {
            "id": run_id,
            "track": track,
            "experiment_id": experiment_id,
            "feature_set": feature_set,
            "feature_columns": feature_cols,
            "goal_metric": goal_metric,
            "goal_value": goal_value,
            "goal_met": False,
            "status": "preflight_failed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "splits": SPLIT_BANNER,
            "preflight": preflight,
            "learning_curve": [],
            "validation_summary": {},
            "error": "Preflight checks failed — fix leakage before running experiments.",
        }
        _save_run(run)
        return run

    if track == "moneyline":
        learning_curve, ml_summary, tot_summary = _walk_forward_validation_moneyline(
            raw, feature_cols
        )
    else:
        learning_curve, tot_summary, ml_summary = _walk_forward_validation_totals(
            raw, feature_cols
        )

    val_summary = {
        "season": VAL_SEASON,
        "moneyline": ml_summary,
        "totals": tot_summary,
    }
    metric_value = _metric_value_for_goal(track, goal_metric, val_summary)
    if metric_value is None:
        goal_ok = False
        within = False
        gap = None
        odds_hint = (
            f"Load {totals_odds_csv_path(VAL_SEASON).name} for market comparison"
            if track == "totals"
            else "metric unavailable on validation"
        )
        goal_note = f"goal_metric '{goal_metric}' unavailable ({odds_hint})"
    else:
        goal_ok = _goal_met(track, goal_metric, metric_value, goal_value)
        within = goal_within_tolerance(
            goal_metric, metric_value, goal_value, tolerance_pct
        )
        gap = goal_gap_pct(goal_metric, float(metric_value), goal_value)
        if within and not goal_ok:
            goal_note = (
                f"Within {tolerance_pct * 100:.0f}% of goal "
                f"(actual={metric_value}, goal={goal_value}, gap={gap * 100:.1f}%)"
            )
        elif goal_ok:
            goal_note = None
        else:
            goal_note = (
                f"Not within {tolerance_pct * 100:.0f}% of goal "
                f"(actual={metric_value}, goal={goal_value}, gap={gap * 100:.1f}%)"
            )

    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "track": track,
        "experiment_id": experiment_id,
        "feature_set": feature_set,
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "goal_metric": goal_metric,
        "goal_value": goal_value,
        "goal_met": goal_ok,
        "goal_within_tolerance": within,
        "goal_tolerance_pct": tolerance_pct,
        "metric_actual_value": metric_value,
        "goal_gap_pct": round(gap, 4) if gap is not None else None,
        "goal_note": goal_note,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "splits": SPLIT_BANNER,
        "preflight": preflight,
        "learning_curve": learning_curve,
        "validation_summary": val_summary,
        "test_confirm": None,
        "promotion_allowed": False,
    }
    _save_run(run)
    return run


def run_until_within_goal(
    experiment_id: str,
    track: str,
    start_feature_set: str,
    goal_metric: str,
    goal_value: float,
    *,
    until_within_pct: float = DEFAULT_GOAL_TOLERANCE_PCT,
) -> dict[str, Any]:
    """
    Try feature sets in queue until validation metric is within tolerance_pct of goal,
    or all sets are exhausted.
    """
    if until_within_pct <= 0:
        return run_experiment(
            experiment_id, track, start_feature_set, goal_metric, goal_value
        )

    _assert_valid_feature_set(track, start_feature_set)
    queue = _feature_set_queue(track, start_feature_set)
    attempts: list[dict[str, Any]] = []
    best_run: dict[str, Any] | None = None
    best_gap = float("inf")

    for idx, feature_set in enumerate(queue[:MAX_QUEUE_ATTEMPTS]):
        attempt_id = experiment_id if idx == 0 else f"{experiment_id}-{idx + 1}"
        run = run_experiment(
            attempt_id,
            track,
            feature_set,
            goal_metric,
            goal_value,
            tolerance_pct=until_within_pct,
        )
        gap = run.get("goal_gap_pct")
        attempts.append(
            {
                "run_id": run["id"],
                "feature_set": feature_set,
                "metric_actual_value": run.get("metric_actual_value"),
                "goal_gap_pct": gap,
                "goal_within_tolerance": run.get("goal_within_tolerance"),
                "goal_met": run.get("goal_met"),
                "status": run.get("status"),
            }
        )
        if gap is not None and gap < best_gap:
            best_gap = gap
            best_run = run
        elif best_run is None:
            best_run = run

        if run.get("goal_within_tolerance"):
            run["campaign"] = {
                "stopped_reason": "within_tolerance",
                "tolerance_pct": until_within_pct,
                "attempts_count": len(attempts),
                "attempts": attempts,
                "feature_sets_tried": [a["feature_set"] for a in attempts],
            }
            _save_run(run)
            return run

    if best_run is None:
        raise ValueError("No lab runs completed — check game data and preflight")

    best_run["campaign"] = {
        "stopped_reason": "queue_exhausted",
        "tolerance_pct": until_within_pct,
        "attempts_count": len(attempts),
        "attempts": attempts,
        "feature_sets_tried": [a["feature_set"] for a in attempts],
        "best_gap_pct": best_run.get("goal_gap_pct"),
    }
    closest = best_run.get("goal_gap_pct")
    closest_pct = f"{closest * 100:.1f}" if closest is not None else "—"
    best_run["goal_note"] = (
        f"Tried {len(attempts)} feature sets; closest gap {closest_pct}% "
        f"— not within {until_within_pct * 100:.0f}% of goal"
    )
    _save_run(best_run)
    return best_run


def _promote_lab_run(run: dict[str, Any]) -> dict[str, Any]:
    """Persist lab run to active production manifest + joblib artifact."""
    from app.data.mlb_games import load_games_with_totals

    track = run.get("track", "moneyline")
    feature_cols = run["feature_columns"]
    feature_set = run["feature_set"]
    run_id = run["id"]
    gate = (run.get("test_confirm") or {}).get("production_gate", {})
    active_gate = gate.get("active_gate_passed", False)
    if not active_gate:
        gate_name = "MODEL.md" if track == "moneyline" else "TOTALS.md"
        raise ValueError(
            f"{gate_name} gate failed on locked 2025 — cannot promote artifact"
        )

    if track == "moneyline":
        artifact = build_moneyline_platt_artifact(
            feature_cols,
            raw=load_games(),
            model_version=f"lab_{feature_set}_platt",
            wave1_pruned_columns=feature_cols,
        )
        manifest = save_moneyline_promotion(run_id, artifact, feature_set=feature_set)
    else:
        artifact = build_totals_artifact(
            feature_cols,
            raw=load_games_with_totals(),
            model_version=f"lab_{feature_set}",
        )
        manifest = save_totals_promotion(run_id, artifact, feature_set=feature_set)
    return manifest


def confirm_locked_test(
    run_id: str,
    *,
    promote: bool = False,
) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    if not run.get("goal_met") and not run.get("goal_within_tolerance"):
        raise ValueError(
            "Run is not at goal and not within tolerance — cannot confirm locked test"
        )
    if run.get("test_confirm") is not None:
        if promote and not run["test_confirm"].get("promoted"):
            manifest = _promote_lab_run(run)
            run["test_confirm"]["promoted"] = True
            run["test_confirm"]["promotion_note"] = (
                f"Promoted to live ({manifest['path']})."
            )
            run["test_confirm"]["active_manifest"] = manifest
            _save_run(run)
        return run

    raw = load_games()
    track = run.get("track", "moneyline")
    feature_cols = run["feature_columns"]
    feat_all = build_features_for_history(raw)
    test_raw = raw[raw["season"] == TEST_SEASON]

    train_feat = feat_all[feat_all["season"] <= VAL_SEASON]
    test_feat = feat_all[feat_all["season"] == TEST_SEASON]
    ml_cols = feature_cols if track == "moneyline" else list(FEATURE_COLUMNS)
    final_model, _ = train_logistic(
        train_feat,
        test_feat,
        ml_cols,
        metrics_name="lab_locked_test",
    )
    ml_block = _score_window_ml(final_model, ml_cols, test_feat, test_raw)

    tot_all = build_totals_features(raw, update_state=True)
    tot_train = tot_all[tot_all["season"] <= VAL_SEASON]
    test_tot = tot_all[tot_all["season"] == TEST_SEASON]
    totals_cols = (
        feature_cols if track == "totals" else list(TOTALS_FEATURE_COLUMNS)
    )
    totals_reg = _fit_totals_regressor(tot_train, totals_cols)
    tot_block = _score_window_totals(
        totals_reg, test_tot, test_raw, totals_cols
    )

    v1_train = prepare_features(
        raw[raw["season"] <= VAL_SEASON],
        _season_era_medians(raw[raw["season"] <= TRAIN_MAX_SEASON]),
        float(DEFAULT_REST_DAYS),
    )
    v1_test = prepare_features(
        test_raw,
        _season_era_medians(raw[raw["season"] <= TRAIN_MAX_SEASON]),
        float(DEFAULT_REST_DAYS),
    )
    _, v1_metrics = train_logistic(v1_train, v1_test, FEATURE_COLUMNS)
    market_ll = ml_block.get("log_loss_market")
    ml_gate = production_gate_passes(
        ml_block["log_loss_model"], v1_metrics.log_loss, market_ll
    )
    totals_gate = totals_production_gate_passes(
        tot_block.get("log_loss_model") or 999.0,
        tot_block.get("log_loss_market"),
    )
    active_gate = ml_gate if track == "moneyline" else totals_gate

    promotion_note = None
    promoted = False
    active_manifest = None
    if promote:
        active_manifest = _promote_lab_run(run)
        promotion_note = f"Promoted to live ({active_manifest['path']})."
        promoted = True

    run["test_confirm"] = {
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "season": TEST_SEASON,
        "track": track,
        "moneyline": ml_block,
        "totals": tot_block,
        "production_gate": {
            "track": track,
            "moneyline_rule": "log_loss < v1 AND log_loss <= market",
            "totals_rule": "totals log_loss <= market",
            "v1_log_loss": round(v1_metrics.log_loss, 4),
            "market_log_loss": market_ll,
            "candidate_log_loss": ml_block["log_loss_model"],
            "candidate_totals_log_loss": tot_block.get("log_loss_model"),
            "production_gate_passed": ml_gate,
            "totals_gate_passed": totals_gate,
            "active_gate_passed": active_gate,
        },
        "promoted": promoted,
        "promotion_note": promotion_note,
        "active_manifest": active_manifest,
    }
    run["promotion_allowed"] = active_gate
    run["status"] = "confirmed"
    _save_run(run)
    return run
