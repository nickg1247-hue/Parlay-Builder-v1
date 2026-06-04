"""Backtest model vs free-market moneylines on 2025 holdout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.models.mlb_baseline import load_games, predict_home_win_proba
from app.odds.mlb_odds_free import ODDS_2025_CSV
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

MARKET_EVAL_CSV = PROJECT_ROOT / "data" / "processed" / "mlb_2025_market_eval.csv"
MARKET_METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_market_metrics.json"
HOLDOUT_SEASON = 2025


def _merge_games_odds(games: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    g = games.copy()
    o = odds.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    g["home_team"] = g["home_team"].map(normalize_team_name)
    g["away_team"] = g["away_team"].map(normalize_team_name)
    o["home_team"] = o["home_team"].map(normalize_team_name)
    o["away_team"] = o["away_team"].map(normalize_team_name)
    return g.merge(o, on=["date", "home_team", "away_team"], how="inner")


def run_market_evaluation(edge_threshold: float = 0.02) -> dict:
    games = load_games()
    holdout = games[games["season"] == HOLDOUT_SEASON].copy()
    if not ODDS_2025_CSV.exists():
        raise FileNotFoundError(
            f"Odds file missing: {ODDS_2025_CSV}. Run scripts/load_mlb_odds_free.py first."
        )
    odds = pd.read_csv(ODDS_2025_CSV)
    matched = _merge_games_odds(holdout, odds)
    valid_odds = matched.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    matched = matched[valid_odds].copy()

    match_rate = len(matched) / len(holdout) if len(holdout) else 0.0
    model_prob = predict_home_win_proba(matched)
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

    def pick_side(row) -> str | None:
        if row.edge_home > edge_threshold and row.edge_away > edge_threshold:
            return "home" if row.edge_home >= row.edge_away else "away"
        if row.edge_home > edge_threshold:
            return "home"
        if row.edge_away > edge_threshold:
            return "away"
        return None

    matched["pick_side"] = matched.apply(pick_side, axis=1)
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
    model_ll = float(log_loss(y_true, np.clip(matched["model_prob_home"], 1e-6, 1 - 1e-6)))
    market_ll = float(
        log_loss(y_true, np.clip(matched["market_prob_home"], 1e-6, 1 - 1e-6))
    )

    plus_ev = matched[matched["is_plus_ev"]]
    n_bets = len(plus_ev)
    total_profit = float(plus_ev["paper_profit"].sum()) if n_bets else 0.0
    roi = total_profit / n_bets if n_bets else 0.0
    hit_rate = float(plus_ev["pick_won"].mean()) if n_bets else 0.0

    MARKET_EVAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_cols = [
        "game_id",
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
    matched[out_cols].to_csv(MARKET_EVAL_CSV, index=False)

    results = {
        "holdout_season": HOLDOUT_SEASON,
        "holdout_games": len(holdout),
        "matched_games": len(matched),
        "match_rate_pct": round(match_rate * 100, 2),
        "edge_threshold": edge_threshold,
        "log_loss_model": round(model_ll, 4),
        "log_loss_market": round(market_ll, 4),
        "model_beats_market_log_loss": model_ll < market_ll,
        "plus_ev_picks": n_bets,
        "paper_trade_roi": round(roi, 4),
        "paper_trade_profit_units": round(total_profit, 2),
        "plus_ev_hit_rate": round(hit_rate, 4),
        "ev_signal": n_bets > 0 and roi > 0,
    }
    MARKET_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def format_summary_table(results: dict) -> str:
    return (
        f"2025 holdout games: {results['holdout_games']}\n"
        f"Matched with odds: {results['matched_games']} "
        f"({results['match_rate_pct']}%)\n"
        f"Log loss — model: {results['log_loss_model']}, "
        f"market: {results['log_loss_market']}\n"
        f"+EV picks (edge > {results['edge_threshold']}): {results['plus_ev_picks']}\n"
        f"Paper ROI (flat $1): {results['paper_trade_roi']:.2%} "
        f"({results['paper_trade_profit_units']} units)\n"
        f"+EV hit rate: {results['plus_ev_hit_rate']:.1%}\n"
        f"EV signal (ROI > 0): {results['ev_signal']}"
    )
