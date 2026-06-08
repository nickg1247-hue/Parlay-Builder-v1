"""Backtest NBA model vs vig-free moneylines on season holdout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.features.nba_pregame import build_features_for_history
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nba_baseline import HOLDOUT_SEASON, load_games, load_model_artifact
from app.odds.nba_odds_free import HOLDOUT_SEASON_END, load_holdout_odds
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds

MARKET_EVAL_JSON = PROJECT_ROOT / "data" / "processed" / "nba_market_eval.json"
MARKET_EVAL_CSV = PROJECT_ROOT / "data" / "processed" / "nba_2026_market_eval.csv"


def _merge_games_odds(games: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    g = games.copy()
    o = odds.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    g["home_team"] = g["home_team"].map(normalize_nba_team_name)
    g["away_team"] = g["away_team"].map(normalize_nba_team_name)
    o["home_team"] = o["home_team"].map(normalize_nba_team_name)
    o["away_team"] = o["away_team"].map(normalize_nba_team_name)
    return g.merge(o, on=["date", "home_team", "away_team"], how="inner")


def _pick_side(row, edge_threshold: float) -> str | None:
    if row.edge_home > edge_threshold and row.edge_away > edge_threshold:
        return "home" if row.edge_home >= row.edge_away else "away"
    if row.edge_home > edge_threshold:
        return "home"
    if row.edge_away > edge_threshold:
        return "away"
    return None


def run_market_evaluation(edge_threshold: float = DEFAULT_MIN_EDGE) -> dict:
    artifact = load_model_artifact()
    model_version = artifact.get("model_version", "unknown")
    feature_cols = artifact["feature_columns"]
    model = artifact["model"]

    games = load_games()
    holdout = games[games["season"] == HOLDOUT_SEASON].copy()
    if holdout.empty:
        raise ValueError(f"No holdout games for season {HOLDOUT_SEASON}")

    holdout_dates = set(pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d"))
    odds = load_holdout_odds(holdout_dates)
    odds_sources: list[str] = []
    if not odds.empty:
        if "odds_source" in odds.columns:
            odds_sources = sorted({str(s) for s in odds["odds_source"].dropna().unique()})
        else:
            odds_sources = ["csv_or_repository"]

    feat = build_features_for_history(games)
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

    matched = _merge_games_odds(holdout_feat, odds)
    valid_odds = matched.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    matched = matched[valid_odds].copy()

    match_rate = len(matched) / len(holdout) if len(holdout) else 0.0
    model_prob = model.predict_proba(matched[feature_cols].values)[:, 1]
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
    matched["pick_side"] = matched.apply(
        lambda r: _pick_side(r, edge_threshold), axis=1
    )
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
        "holdout_season_end": HOLDOUT_SEASON_END,
        "holdout_season_label": "2025-26",
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
        "clv_required": True,
        "betting_ready": False,
        "advisor_note": (
            "Paper-trade ROI on matched holdout is not betting-ready. "
            "Forward CLV (NBA-CLV) required before any real-money claim."
        ),
    }
    _write_outputs(matched, results)
    return results


def _empty_results(
    *,
    model_version: str,
    holdout_games: int,
    edge_threshold: float,
    odds_sources: list[str],
) -> dict:
    return {
        "model_version": model_version,
        "holdout_season_end": HOLDOUT_SEASON_END,
        "holdout_season_label": "2025-26",
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
        "clv_required": True,
        "betting_ready": False,
        "advisor_note": (
            "No holdout odds matched. Import free CSV via scripts/load_nba_odds_free.py "
            "or capture live lines into data/processed/nba_odds_repository/ (no bulk historical API)."
        ),
    }


def _write_outputs(matched: pd.DataFrame, results: dict) -> None:
    MARKET_EVAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    MARKET_EVAL_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if matched.empty:
        return
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


def format_summary_table(results: dict) -> str:
    ll_model = results.get("log_loss_model")
    ll_market = results.get("log_loss_market")
    ll_model_s = f"{ll_model:.4f}" if ll_model is not None else "—"
    ll_market_s = f"{ll_market:.4f}" if ll_market is not None else "—"
    sources = ", ".join(results.get("odds_sources") or []) or "none"
    return (
        f"Model: {results.get('model_version', 'unknown')}\n"
        f"Holdout: {results.get('holdout_season_label')} "
        f"({results['holdout_games']} games)\n"
        f"Matched with odds: {results['matched_games']} "
        f"({results['match_rate_pct']}%) — sources: {sources}\n"
        f"Log loss — model: {ll_model_s}, market: {ll_market_s}\n"
        f"+EV picks (edge > {results['edge_threshold']}): {results['plus_ev_picks']}\n"
        f"Paper ROI (flat $1): {results['paper_trade_roi']:.2%} "
        f"({results['paper_trade_profit_units']} units)\n"
        f"+EV hit rate: {results['plus_ev_hit_rate']:.1%}\n"
        f"EV signal (ROI > 0): {results['ev_signal']}\n"
        f"Betting-ready: {results.get('betting_ready')} "
        f"(forward CLV required)"
    )
