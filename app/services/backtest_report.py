"""Rolling-window MLB backtest report (moneyline + totals)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.data.mlb_games import load_games_with_totals
from app.features.mlb_pregame import build_features_for_history
from app.features.mlb_totals_pregame import build_totals_features
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.mlb_baseline import predict_home_win_proba
from app.models.mlb_totals import (
    actual_went_over,
    edge_flagged_hit_rate,
    load_totals_artifact,
    prob_over_poisson,
)
from app.odds.market_eval import _merge_games_odds
from app.odds.mlb_odds_free import ODDS_2025_CSV, TOTALS_2025_CSV
from app.odds.odds_math import market_probs_from_american, market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_backtest_report.json"
EVAL_SEASON = 2025


def _empty_report(days: int, error: str | None = None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "start_date": None,
        "end_date": None,
        "games_in_window": 0,
        "moneyline": {
            "games_with_odds": 0,
            "winner_accuracy_pct": 0.0,
            "plus_ev_picks": 0,
            "plus_ev_accuracy_pct": 0.0,
            "log_loss_model": 0.0,
            "log_loss_market": 0.0,
            "model_beats_market": False,
            "min_edge": DEFAULT_MIN_EDGE,
        },
        "totals": {
            "games_with_ou_line": 0,
            "ou_pick_accuracy_pct": 0.0,
            "plus_ev_ou_picks": 0,
            "plus_ev_ou_accuracy_pct": 0.0,
            "total_runs_mae": 0.0,
            "total_runs_bias": 0.0,
            "min_edge": DEFAULT_MIN_EDGE,
        },
        "status": "error" if error else "ok",
        "error": error,
    }


def _pick_moneyline_side(row, min_edge: float) -> str | None:
    if row.edge_home > min_edge and row.edge_away > min_edge:
        return "home" if row.edge_home >= row.edge_away else "away"
    if row.edge_home > min_edge:
        return "home"
    if row.edge_away > min_edge:
        return "away"
    return None


def _moneyline_metrics(
    window_games: pd.DataFrame,
    hist: pd.DataFrame,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> tuple[dict[str, Any], str | None]:
    if not ODDS_2025_CSV.exists():
        return _empty_report(0)["moneyline"], "Moneyline odds CSV missing"

    odds = pd.read_csv(ODDS_2025_CSV)
    matched = _merge_games_odds(window_games, odds)
    valid = matched.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    matched = matched[valid].copy()
    if matched.empty:
        return _empty_report(0)["moneyline"], None

    combined = pd.concat([hist, window_games], ignore_index=True)
    feat = build_features_for_history(combined)
    win_ids = set(window_games["game_id"].astype(str))
    win_feat = feat[feat["game_id"].astype(str).isin(win_ids)].copy()
    matched["game_id"] = matched["game_id"].astype(str)
    win_feat["game_id"] = win_feat["game_id"].astype(str)
    scored = matched.merge(win_feat, on="game_id", how="inner", suffixes=("_odds", ""))

    model_prob = predict_home_win_proba(scored)
    matched = scored.copy()
    matched["model_prob_home"] = model_prob
    matched["model_prob_away"] = 1.0 - model_prob

    market_home: list[float] = []
    market_away: list[float] = []
    for row in matched.itertuples(index=False):
        mh, ma = market_probs_from_american(int(row.home_ml), int(row.away_ml))
        market_home.append(mh)
        market_away.append(ma)
    matched["market_prob_home"] = market_home
    matched["market_prob_away"] = market_away
    matched["edge_home"] = matched["model_prob_home"] - matched["market_prob_home"]
    matched["edge_away"] = matched["model_prob_away"] - matched["market_prob_away"]

    y_true = matched["home_win"].astype(int).values
    model_ll = float(
        log_loss(y_true, np.clip(matched["model_prob_home"], 1e-6, 1 - 1e-6))
    )
    market_ll = float(
        log_loss(y_true, np.clip(matched["market_prob_home"], 1e-6, 1 - 1e-6))
    )

    model_pick_home = matched["model_prob_home"] >= 0.5
    winner_acc = float((model_pick_home == (matched["home_win"] == 1)).mean() * 100)

    matched["pick_side"] = matched.apply(
        lambda r: _pick_moneyline_side(r, min_edge), axis=1
    )
    plus_ev = matched[matched["pick_side"].notna()]
    pick_wins = []
    for row in plus_ev.itertuples(index=False):
        if row.pick_side == "home":
            pick_wins.append(int(row.home_win) == 1)
        else:
            pick_wins.append(int(row.home_win) == 0)
    plus_ev_acc = (
        float(np.mean(pick_wins) * 100) if pick_wins else 0.0
    )

    return {
        "games_with_odds": len(matched),
        "winner_accuracy_pct": round(winner_acc, 2),
        "plus_ev_picks": len(plus_ev),
        "plus_ev_accuracy_pct": round(plus_ev_acc, 2),
        "log_loss_model": round(model_ll, 4),
        "log_loss_market": round(market_ll, 4),
        "model_beats_market": model_ll < market_ll,
        "min_edge": min_edge,
    }, None


def _merge_totals_odds(games: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    holdout = games.copy()
    holdout["date"] = pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d")
    holdout["home_team"] = holdout["home_team"].map(normalize_team_name)
    holdout["away_team"] = holdout["away_team"].map(normalize_team_name)
    o = odds.copy()
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    o["home_team"] = o["home_team"].map(normalize_team_name)
    o["away_team"] = o["away_team"].map(normalize_team_name)
    return holdout.merge(o, on=["date", "home_team", "away_team"], how="inner")


def _totals_metrics(
    window_games: pd.DataFrame,
    hist: pd.DataFrame,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> tuple[dict[str, Any], str | None]:
    if not TOTALS_2025_CSV.exists():
        return _empty_report(0)["totals"], "Totals odds CSV missing"

    odds = pd.read_csv(TOTALS_2025_CSV)
    combined = pd.concat([hist, window_games], ignore_index=True)
    feat = build_totals_features(combined, update_state=True)
    win_ids = set(window_games["game_id"].astype(str))
    eval_df = feat[feat["game_id"].astype(str).isin(win_ids)].copy()
    merged = _merge_totals_odds(eval_df, odds)
    valid = merged.apply(
        lambda r: is_valid_american_odds(r.over_odds)
        and is_valid_american_odds(r.under_odds),
        axis=1,
    )
    merged = merged[valid].copy()
    if merged.empty:
        return _empty_report(0)["totals"], None

    artifact = load_totals_artifact()
    reg = artifact["model"]
    cols = artifact["feature_columns"]
    merged["expected_total_runs"] = reg.predict(merged[cols].values)
    merged["model_prob_over"] = [
        prob_over_poisson(float(mu), float(line))
        for mu, line in zip(merged["expected_total_runs"], merged["ou_line"])
    ]
    market_o = []
    for row in merged.itertuples(index=False):
        mo, _ = market_probs_from_american_totals(int(row.over_odds), int(row.under_odds))
        market_o.append(mo)
    merged["market_prob_over"] = market_o
    merged["went_over"] = merged.apply(
        lambda r: actual_went_over(r.total_runs, float(r.ou_line)), axis=1
    )

    pred = merged["expected_total_runs"].astype(float)
    actual = merged["total_runs"].astype(float)
    mae = float(np.mean(np.abs(pred - actual)))
    bias = float(np.mean(pred - actual))

    model_pick_over = merged["model_prob_over"] >= 0.5
    ou_acc = float(
        (model_pick_over == (merged["went_over"] == 1)).mean() * 100
    )

    edges = merged["model_prob_over"] - merged["market_prob_over"]
    plus_ev_mask = edges.abs() >= min_edge
    plus_ev_n = int(plus_ev_mask.sum())
    hit = edge_flagged_hit_rate(
        merged, "model_prob_over", "market_prob_over", min_edge=min_edge
    )
    plus_ev_acc = float(hit * 100) if hit is not None else 0.0

    return {
        "games_with_ou_line": len(merged),
        "ou_pick_accuracy_pct": round(ou_acc, 2),
        "plus_ev_ou_picks": plus_ev_n,
        "plus_ev_ou_accuracy_pct": round(plus_ev_acc, 2),
        "total_runs_mae": round(mae, 3),
        "total_runs_bias": round(bias, 3),
        "min_edge": min_edge,
    }, None


def run_backtest_report(
    days: int = 30,
    *,
    write_cache: bool = True,
) -> dict[str, Any]:
    games = load_games_with_totals()
    season = games[games["season"] == EVAL_SEASON].copy()
    completed = season[season["home_score"].notna() & season["away_score"].notna()]
    if completed.empty:
        return _empty_report(days, "No completed games for evaluation season")

    max_game_date = pd.to_datetime(completed["date"]).max()
    eval_end = max_game_date
    clip_note: str | None = None
    odds_caps: list[pd.Timestamp] = []
    if ODDS_2025_CSV.exists():
        odds_caps.append(pd.to_datetime(pd.read_csv(ODDS_2025_CSV, usecols=["date"])["date"]).max())
    if TOTALS_2025_CSV.exists():
        odds_caps.append(
            pd.to_datetime(pd.read_csv(TOTALS_2025_CSV, usecols=["date"])["date"]).max()
        )
    if odds_caps:
        odds_end = min(odds_caps)
        if odds_end < eval_end:
            eval_end = odds_end
            clip_note = (
                f"Window end clipped to last odds date ({eval_end.strftime('%Y-%m-%d')})"
            )

    start = eval_end - timedelta(days=days)
    dates = pd.to_datetime(completed["date"])
    window = completed[(dates >= start) & (dates <= eval_end)].copy()
    hist = completed[dates < start].copy()

    errors: list[str] = []
    if clip_note:
        errors.append(clip_note)
    ml_block, ml_err = _moneyline_metrics(window, hist)
    if ml_err:
        errors.append(ml_err)

    tot_block, tot_err = _totals_metrics(window, hist)
    if tot_err:
        errors.append(tot_err)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": eval_end.strftime("%Y-%m-%d"),
        "games_in_window": len(window),
        "moneyline": ml_block,
        "totals": tot_block,
        "status": "ok",
        "error": "; ".join(errors) if errors else None,
    }
    if errors and report["moneyline"]["games_with_odds"] == 0 and report["totals"]["games_with_ou_line"] == 0:
        report["status"] = "error"

    if write_cache:
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_saved_backtest_report() -> dict[str, Any]:
    if not REPORT_JSON.exists():
        return _empty_report(30, "No saved report. Click Run backtest.")
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))
