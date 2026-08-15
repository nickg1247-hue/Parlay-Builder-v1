"""Backtest NFL model vs vig-free market on holdout (ESPN ingest lines + live repo)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nfl_baseline import HOLDOUT_SEASON, load_games, load_model_artifact, predict_home_win_proba
from app.odds.nfl_odds_repository import repository_odds_dataframe
from app.odds.nfl_team_aliases import normalize_nfl_team
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds

MARKET_METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_market_metrics.json"


def _espn_holdout_lines(games: pd.DataFrame) -> pd.DataFrame:
    if "espn_home_ml" not in games.columns:
        return pd.DataFrame()
    rows = games[
        games["espn_home_ml"].notna()
        & games["espn_away_ml"].notna()
        & (games["season"] == HOLDOUT_SEASON)
    ].copy()
    if rows.empty:
        return pd.DataFrame()
    rows["date"] = pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d")
    rows["home_team"] = rows["home_team_abbr"].map(normalize_nfl_team)
    rows["away_team"] = rows["away_team_abbr"].map(normalize_nfl_team)
    rows["home_ml"] = rows["espn_home_ml"].astype(int)
    rows["away_ml"] = rows["espn_away_ml"].astype(int)
    return rows[["date", "home_team", "away_team", "home_ml", "away_ml", "game_id", "home_win"]]


def run_market_evaluation(edge_threshold: float = DEFAULT_MIN_EDGE) -> dict:
    games = load_games()
    holdout = games[games["season"] == HOLDOUT_SEASON].copy()
    espn_lines = _espn_holdout_lines(holdout)
    repo = repository_odds_dataframe()
    if not repo.empty:
        repo["home_team"] = repo["home_team"].map(normalize_nfl_team)
        repo["away_team"] = repo["away_team"].map(normalize_nfl_team)
    frames = [df for df in (espn_lines, repo) if not df.empty]
    if not frames:
        results = {
            "holdout_season": HOLDOUT_SEASON,
            "matched_games": 0,
            "note": "No ESPN ingest lines or Odds API repository snapshots for holdout.",
            "model_beats_market_log_loss": None,
        }
        MARKET_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return results

    odds = pd.concat(frames, ignore_index=True)
    if "game_id" not in odds.columns:
        holdout = holdout.copy()
        holdout["date_key"] = pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d")
        holdout["home_key"] = holdout["home_team_abbr"].map(normalize_nfl_team)
        holdout["away_key"] = holdout["away_team_abbr"].map(normalize_nfl_team)
        odds = odds.merge(
            holdout[["game_id", "date_key", "home_key", "away_key", "home_win"]],
            left_on=["date", "home_team", "away_team"],
            right_on=["date_key", "home_key", "away_key"],
            how="inner",
        )
    matched = odds.dropna(subset=["home_ml", "away_ml", "home_win"]).copy()
    matched = matched[
        matched.apply(
            lambda r: is_valid_american_odds(r["home_ml"]) and is_valid_american_odds(r["away_ml"]),
            axis=1,
        )
    ]
    if matched.empty:
        results = {
            "holdout_season": HOLDOUT_SEASON,
            "matched_games": 0,
            "note": "Lines present but none matched holdout games.",
            "model_beats_market_log_loss": None,
        }
        MARKET_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return results

    slate = matched[["game_id", "date", "home_team", "away_team"]].copy()
    slate["season"] = HOLDOUT_SEASON
    slate["home_team_abbr"] = slate["home_team"]
    slate["away_team_abbr"] = slate["away_team"]
    try:
        probs = predict_home_win_proba(slate)
    except FileNotFoundError:
        artifact = load_model_artifact()
        del artifact
        probs = np.full(len(slate), 0.5)
    matched = matched.copy()
    matched["model_prob_home"] = probs
    mkt = [market_probs_from_american(int(r.home_ml), int(r.away_ml)) for r in matched.itertuples()]
    matched["market_prob_home"] = [p[0] for p in mkt]
    matched["edge_home"] = matched["model_prob_home"] - matched["market_prob_home"]
    y = matched["home_win"].astype(int).values
    model_ll = float(log_loss(y, np.clip(matched["model_prob_home"].values, 1e-6, 1 - 1e-6)))
    market_ll = float(log_loss(y, np.clip(matched["market_prob_home"].values, 1e-6, 1 - 1e-6)))

    picks = []
    pnl = 0.0
    for row in matched.itertuples(index=False):
        if row.edge_home >= edge_threshold:
            won = int(row.home_win) == 1
            pnl += american_payout_profit(int(row.home_ml), won)
            picks.append(1)
        elif (1 - row.model_prob_home) - (1 - row.market_prob_home) >= edge_threshold:
            won = int(row.home_win) == 0
            pnl += american_payout_profit(int(row.away_ml), won)
            picks.append(1)
    n_picks = len(picks)
    results = {
        "holdout_season": HOLDOUT_SEASON,
        "matched_games": int(len(matched)),
        "model_log_loss": round(model_ll, 4),
        "market_log_loss": round(market_ll, 4),
        "model_beats_market_log_loss": model_ll < market_ll,
        "plus_ev_picks": n_picks,
        "paper_pnl_units": round(pnl, 3) if n_picks else 0.0,
        "edge_threshold": edge_threshold,
        "note": "Advisory only. ESPN scoreboard lines + live Odds API snapshots; not closing lines.",
    }
    MARKET_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def format_summary_table(results: dict) -> str:
    matched = results.get("matched_games", 0)
    if not matched:
        return (
            f"Holdout: {results.get('holdout_season')}\n"
            f"Matched games: 0\n"
            f"{results.get('note') or 'No market lines matched.'}"
        )
    return (
        f"Holdout: {results.get('holdout_season')}\n"
        f"Matched games: {matched}\n"
        f"Log loss — model: {results.get('model_log_loss')}, "
        f"market: {results.get('market_log_loss')}\n"
        f"Model beats market log loss: {results.get('model_beats_market_log_loss')}\n"
        f"+EV picks (edge > {results.get('edge_threshold')}): {results.get('plus_ev_picks')}\n"
        f"Paper PnL (flat $1): {results.get('paper_pnl_units')} units\n"
        f"{results.get('note') or ''}"
    )
