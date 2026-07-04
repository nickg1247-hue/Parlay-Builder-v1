"""Backtest UFC model vs vig-free moneylines on 2024 holdout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.features.ufc_pregame import build_features_for_history
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.ufc_baseline import HOLDOUT_SEASON, load_fights, load_model_artifact, predict_home_win_proba
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.odds.ufc_fighter_aliases import normalize_fighter_name
from app.odds.ufc_odds_free import HOLDOUT_SEASON as ODDS_HOLDOUT_SEASON, load_holdout_odds

MARKET_EVAL_JSON = PROJECT_ROOT / "data" / "processed" / "ufc_market_metrics.json"
MARKET_EVAL_CSV = PROJECT_ROOT / "data" / "processed" / "ufc_2024_market_eval.csv"


def _merge_fights_odds(fights: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    g = fights.copy()
    o = odds.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    g["home_team"] = g["home_team"].map(normalize_fighter_name)
    g["away_team"] = g["away_team"].map(normalize_fighter_name)
    o["home_team"] = o["home_team"].map(normalize_fighter_name)
    o["away_team"] = o["away_team"].map(normalize_fighter_name)
    return g.merge(o, on=["date", "home_team", "away_team"], how="inner")


def _pick_side(row, edge_threshold: float) -> str | None:
    if row.edge_home > edge_threshold and row.edge_away > edge_threshold:
        return "home" if row.edge_home >= row.edge_away else "away"
    if row.edge_home > edge_threshold:
        return "home"
    if row.edge_away > edge_threshold:
        return "away"
    return None


def _empty_results(
    *,
    model_version: str,
    holdout_games: int,
    edge_threshold: float,
    odds_sources: list[str],
) -> dict:
    return {
        "model_version": model_version,
        "holdout_season": ODDS_HOLDOUT_SEASON,
        "holdout_games": holdout_games,
        "matched_games": 0,
        "match_rate_pct": 0.0,
        "odds_sources": odds_sources,
        "edge_threshold": edge_threshold,
        "log_loss_model": None,
        "log_loss_market": None,
        "model_beats_market_log_loss": None,
        "plus_ev_picks": 0,
        "paper_trade_roi": 0.0,
        "paper_trade_profit_units": 0.0,
        "plus_ev_hit_rate": 0.0,
        "ev_signal": False,
        "status": "no_odds",
        "advisor_note": (
            "No historical UFC moneylines matched. Import CSV via "
            "scripts/load_ufc_odds_free.py or capture live odds to ufc_odds_repository."
        ),
    }


def _write_outputs(matched: pd.DataFrame, results: dict) -> None:
    MARKET_EVAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not matched.empty:
        out_cols = [
            "fight_id",
            "date",
            "home_team",
            "away_team",
            "home_win",
            "home_ml",
            "away_ml",
            "model_prob_home",
            "market_prob_home",
            "edge_home",
            "edge_away",
            "pick_side",
            "is_plus_ev",
            "paper_profit",
            "pick_won",
        ]
        available = [c for c in out_cols if c in matched.columns]
        matched[available].to_csv(MARKET_EVAL_CSV, index=False)
    MARKET_EVAL_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")


def run_market_evaluation(edge_threshold: float = DEFAULT_MIN_EDGE) -> dict:
    artifact = load_model_artifact()
    model_version = artifact.get("model_version", "unknown")

    fights = load_fights()
    holdout = fights[fights["season"] == HOLDOUT_SEASON].copy()
    if holdout.empty:
        raise ValueError(f"No holdout fights for season {HOLDOUT_SEASON}")

    holdout_dates = set(pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d"))
    odds = load_holdout_odds(holdout_dates)
    odds_sources: list[str] = []
    if not odds.empty:
        if "odds_source" in odds.columns:
            odds_sources = sorted({str(s) for s in odds["odds_source"].dropna().unique()})
        else:
            odds_sources = ["csv_or_repository"]

    feat = build_features_for_history(fights)
    holdout_feat = feat[feat["season"] == HOLDOUT_SEASON].copy()

    if odds.empty:
        results = _empty_results(
            model_version=model_version,
            holdout_games=len(holdout),
            edge_threshold=edge_threshold,
            odds_sources=odds_sources,
        )
        _write_outputs(pd.DataFrame(), results)
        return results

    matched = _merge_fights_odds(holdout_feat, odds)
    valid_odds = matched.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    matched = matched[valid_odds].copy()

    match_rate = len(matched) / len(holdout) if len(holdout) else 0.0
    model_prob = np.asarray(predict_home_win_proba(matched), dtype=float)
    matched = matched.copy()
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
    matched["pick_side"] = matched.apply(lambda r: _pick_side(r, edge_threshold), axis=1)
    matched["is_plus_ev"] = matched["pick_side"].notna()

    profits: list[float] = []
    pick_wins: list[int] = []
    for row in matched.itertuples(index=False):
        if row.pick_side is None:
            profits.append(0.0)
            pick_wins.append(0)
            continue
        if row.pick_side == "home":
            won = int(row.home_win) == 1
            profits.append(american_payout_profit(row.home_ml, won))
            pick_wins.append(int(won))
        else:
            won = int(row.home_win) == 0
            profits.append(american_payout_profit(row.away_ml, won))
            pick_wins.append(int(won))

    matched["paper_profit"] = profits
    matched["pick_won"] = pick_wins

    y_true = matched["home_win"].values
    model_ll = float(
        log_loss(y_true, np.clip(matched["model_prob_home"], 1e-6, 1 - 1e-6))
    )
    market_ll = float(
        log_loss(y_true, np.clip(matched["market_prob_home"], 1e-6, 1 - 1e-6))
    )

    plus_ev = matched[matched["is_plus_ev"]]
    n_bets = len(plus_ev)
    total_profit = float(plus_ev["paper_profit"].sum()) if n_bets else 0.0
    roi = total_profit / n_bets if n_bets else 0.0
    hit_rate = float(plus_ev["pick_won"].mean()) if n_bets else 0.0

    results = {
        "model_version": model_version,
        "holdout_season": HOLDOUT_SEASON,
        "holdout_games": len(holdout),
        "matched_games": len(matched),
        "match_rate_pct": round(match_rate * 100, 2),
        "odds_sources": odds_sources,
        "edge_threshold": edge_threshold,
        "log_loss_model": round(model_ll, 4),
        "log_loss_market": round(market_ll, 4),
        "model_beats_market_log_loss": model_ll < market_ll,
        "plus_ev_picks": n_bets,
        "paper_trade_roi": round(roi, 4),
        "paper_trade_profit_units": round(total_profit, 2),
        "plus_ev_hit_rate": round(hit_rate, 4),
        "ev_signal": n_bets > 0 and roi > 0,
        "betting_ready": False,
        "advisor_note": (
            "Paper-trade ROI on matched holdout is not betting-ready. "
            "Forward CLV (ufc forward_clv) required before any real-money claim."
        ),
    }
    _write_outputs(matched, results)
    return results


def format_summary_table(results: dict) -> str:
    return (
        f"Model: {results.get('model_version', 'unknown')}\n"
        f"{results.get('holdout_season', HOLDOUT_SEASON)} holdout fights: {results['holdout_games']}\n"
        f"Matched with odds: {results['matched_games']} "
        f"({results.get('match_rate_pct', 0)}%)\n"
        f"Log loss — model: {results.get('log_loss_model')}, "
        f"market: {results.get('log_loss_market')}\n"
        f"+EV picks (edge > {results['edge_threshold']}): {results['plus_ev_picks']}\n"
        f"Paper ROI (flat $1): {results.get('paper_trade_roi', 0):.2%} "
        f"({results.get('paper_trade_profit_units', 0)} units)\n"
        f"+EV hit rate: {results.get('plus_ev_hit_rate', 0):.1%}\n"
    )
