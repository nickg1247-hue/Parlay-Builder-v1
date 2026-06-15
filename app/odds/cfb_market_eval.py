"""Backtest CFB model vs vig-free market on season holdout."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.features.cfb_pregame import build_features_for_history
from app.models.cfb_baseline import HOLDOUT_SEASON, load_games, load_model_artifact
from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.cfb_betting_lines import load_cfbd_holdout_lines
from app.odds.cfb_odds_repository import repository_odds_dataframe
from app.odds.cfb_team_aliases import normalize_team_name
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.spread_math import side_covers
from app.odds.team_aliases import is_valid_american_odds

MARKET_METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_market_metrics.json"
MARKET_EVAL_CSV = PROJECT_ROOT / "data" / "processed" / "cfb_market_eval.csv"


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


def _valid_moneyline_row(row) -> bool:
    hm, am = row.get("home_ml"), row.get("away_ml")
    if hm is None or am is None:
        return False
    if isinstance(hm, float) and pd.isna(hm):
        return False
    if isinstance(am, float) and pd.isna(am):
        return False
    return is_valid_american_odds(hm) and is_valid_american_odds(am)


def _pick_side(row, edge_threshold: float) -> str | None:
    if row.edge_home > edge_threshold and row.edge_away > edge_threshold:
        return "home" if row.edge_home >= row.edge_away else "away"
    if row.edge_home > edge_threshold:
        return "home"
    if row.edge_away > edge_threshold:
        return "away"
    return None


def load_holdout_odds(holdout_dates: set[str] | None = None) -> pd.DataFrame:
    """CFBD lines cache first, then live-captured cfb_odds_repository snapshots."""
    cfbd = load_cfbd_holdout_lines(holdout_dates)
    repo = repository_odds_dataframe(holdout_dates)
    frames = [df for df in (cfbd, repo) if not df.empty]
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        by=["date", "home_team", "away_team"],
        ascending=True,
    )
    combined = combined.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="last"
    )
    return combined.reset_index(drop=True)


def run_market_evaluation(edge_threshold: float = DEFAULT_MIN_EDGE) -> dict:
    artifact = load_model_artifact()
    model_version = artifact.get("model_version", "unknown")
    model = artifact["model"]

    games = load_games()
    holdout = games[games["season"] == HOLDOUT_SEASON].copy()
    if holdout.empty:
        raise ValueError(f"No holdout games for season {HOLDOUT_SEASON}")

    holdout_dates = set(pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d"))
    odds = load_holdout_odds(holdout_dates)
    odds_sources: list[str] = []
    if not odds.empty and "odds_source" in odds.columns:
        odds_sources = sorted({str(s) for s in odds["odds_source"].dropna().unique()})

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
    score_cols = holdout[["game_id", "home_score", "away_score"]].drop_duplicates(
        subset=["game_id"]
    )
    matched = matched.merge(score_cols, on="game_id", how="left")
    valid_ml = matched.apply(_valid_moneyline_row, axis=1)
    ml_matched = matched[valid_ml].copy()

    match_rate = len(matched) / len(holdout) if len(holdout) else 0.0
    ml_match_rate = len(ml_matched) / len(holdout) if len(holdout) else 0.0

    if ml_matched.empty:
        results = _empty_results(
            model_version=model_version,
            holdout_games=len(holdout),
            edge_threshold=edge_threshold,
            odds_sources=odds_sources,
        )
        results["matched_games"] = len(matched)
        results["match_rate_pct"] = round(match_rate * 100, 2)
        _write_outputs(matched, results)
        return results

    model_prob = model.predict_proba(ml_matched[artifact["feature_columns"]].values)[:, 1]
    ml_matched = ml_matched.copy()
    ml_matched["model_prob_home"] = model_prob
    ml_matched["model_prob_away"] = 1.0 - model_prob

    market_home: list[float] = []
    market_away: list[float] = []
    for row in ml_matched.itertuples(index=False):
        mh, ma = market_probs_from_american(int(row.home_ml), int(row.away_ml))
        market_home.append(mh)
        market_away.append(ma)
    ml_matched["market_prob_home"] = market_home
    ml_matched["market_prob_away"] = market_away
    ml_matched["edge_home"] = ml_matched["model_prob_home"] - ml_matched["market_prob_home"]
    ml_matched["edge_away"] = ml_matched["model_prob_away"] - ml_matched["market_prob_away"]
    ml_matched["pick_side"] = ml_matched.apply(
        lambda r: _pick_side(r, edge_threshold), axis=1
    )
    ml_matched["is_plus_ev"] = ml_matched["pick_side"].notna()

    profits: list[float] = []
    pick_wins: list[int] = []
    for row in ml_matched.itertuples(index=False):
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

    ml_matched["paper_profit"] = profits
    ml_matched["pick_won"] = pick_wins

    y_true = ml_matched["home_win"].values
    model_ll = float(
        log_loss(y_true, np.clip(ml_matched["model_prob_home"], 1e-6, 1 - 1e-6))
    )
    market_ll = float(
        log_loss(y_true, np.clip(ml_matched["market_prob_home"], 1e-6, 1 - 1e-6))
    )
    brier = float(np.mean((ml_matched["model_prob_home"] - y_true) ** 2))
    accuracy = float(np.mean((ml_matched["model_prob_home"] >= 0.5) == y_true))

    plus_ev = ml_matched[ml_matched["is_plus_ev"]]
    n_bets = len(plus_ev)
    total_profit = float(plus_ev["paper_profit"].sum()) if n_bets else 0.0
    roi = total_profit / n_bets if n_bets else 0.0
    hit_rate = float(plus_ev["pick_won"].mean()) if n_bets else 0.0

    spread_ll = _spread_cover_log_loss(matched)
    totals_ll = _totals_over_log_loss(matched)

    results = {
        "model_version": model_version,
        "holdout_season": HOLDOUT_SEASON,
        "holdout_games": len(holdout),
        "matched_games": len(matched),
        "matched_ml_games": len(ml_matched),
        "match_rate_pct": round(match_rate * 100, 2),
        "ml_match_rate_pct": round(ml_match_rate * 100, 2),
        "odds_sources": odds_sources,
        "edge_threshold": edge_threshold,
        "log_loss_model": round(model_ll, 4),
        "log_loss_market": round(market_ll, 4),
        "brier_model": round(brier, 4),
        "accuracy_model": round(accuracy, 4),
        "model_beats_market_log_loss": model_ll < market_ll,
        "plus_ev_picks": n_bets,
        "paper_trade_roi": round(roi, 4),
        "paper_trade_profit_units": round(total_profit, 2),
        "plus_ev_hit_rate": round(hit_rate, 4),
        "spread_cover_log_loss": spread_ll,
        "totals_over_log_loss": totals_ll,
        "ev_signal": n_bets > 0 and roi > 0,
        "clv_required": True,
        "betting_ready": False,
        "advisor_note": (
            "Paper-trade ROI on matched holdout is not betting-ready. "
            "Forward CLV required before any real-money claim."
        ),
    }
    _write_outputs(ml_matched, results)
    return results


def _spread_cover_log_loss(matched: pd.DataFrame) -> float | None:
    if "home_spread_point" not in matched.columns:
        return None
    rows = matched[matched["home_spread_point"].notna()].copy()
    if rows.empty or "home_score" not in rows.columns:
        return None
    try:
        from app.models.cfb_margin import predict_spread_covers
    except FileNotFoundError:
        return None

    slate = rows[
        ["game_id", "date", "season", "home_team", "away_team", "home_spread_point"]
    ].copy()
    spread_preds = predict_spread_covers(slate)
    pred_by_id = {
        str(r.game_id): float(r.model_prob_home_cover)
        for r in spread_preds.itertuples(index=False)
    }
    probs: list[float] = []
    outcomes: list[int] = []
    for row in rows.itertuples(index=False):
        gid = str(row.game_id)
        prob = pred_by_id.get(gid)
        if prob is None:
            continue
        margin = float(row.home_score) - float(row.away_score)
        sp = float(row.home_spread_point)
        away_sp = -sp
        probs.append(prob)
        outcomes.append(
            int(
                side_covers(
                    "home",
                    float(row.home_score),
                    float(row.away_score),
                    sp,
                    away_sp,
                )
            )
        )
    if not probs:
        return None
    return float(log_loss(outcomes, np.clip(probs, 1e-6, 1 - 1e-6)))


def _totals_over_log_loss(matched: pd.DataFrame) -> float | None:
    if "ou_line" not in matched.columns:
        return None
    rows = matched[
        matched["ou_line"].notna()
        & matched["home_score"].notna()
        & matched["away_score"].notna()
    ].copy()
    if rows.empty:
        return None
    try:
        from app.models.cfb_totals import enrich_totals_columns
    except FileNotFoundError:
        return None

    slate = rows[["game_id", "date", "season", "home_team", "away_team", "ou_line"]].copy()
    totals_preds = enrich_totals_columns(slate)
    pred_by_id = {
        str(r.game_id): float(r.model_prob_over)
        for r in totals_preds.itertuples(index=False)
    }
    probs: list[float] = []
    outcomes: list[int] = []
    for row in rows.itertuples(index=False):
        gid = str(row.game_id)
        prob = pred_by_id.get(gid)
        if prob is None:
            continue
        total_pts = float(row.home_score) + float(row.away_score)
        outcomes.append(int(total_pts > float(row.ou_line)))
        probs.append(prob)
    if not probs:
        return None
    return float(log_loss(outcomes, np.clip(probs, 1e-6, 1 - 1e-6)))


def _empty_results(
    *,
    model_version: str,
    holdout_games: int,
    edge_threshold: float,
    odds_sources: list[str],
) -> dict:
    return {
        "model_version": model_version,
        "holdout_season": HOLDOUT_SEASON,
        "holdout_games": holdout_games,
        "matched_games": 0,
        "matched_ml_games": 0,
        "match_rate_pct": 0.0,
        "ml_match_rate_pct": 0.0,
        "odds_sources": odds_sources,
        "edge_threshold": edge_threshold,
        "log_loss_model": None,
        "log_loss_market": None,
        "brier_model": None,
        "accuracy_model": None,
        "model_beats_market_log_loss": None,
        "plus_ev_picks": 0,
        "paper_trade_roi": 0.0,
        "paper_trade_profit_units": 0.0,
        "plus_ev_hit_rate": 0.0,
        "spread_cover_log_loss": None,
        "totals_over_log_loss": None,
        "ev_signal": False,
        "clv_required": True,
        "betting_ready": False,
        "advisor_note": (
            "No holdout odds matched. Populate data/processed/cfb_lines_cache/ via CFBD "
            "or capture live lines to data/processed/cfb_odds_repository/."
        ),
    }


def _write_outputs(matched: pd.DataFrame, results: dict) -> None:
    MARKET_METRICS_JSON.parent.mkdir(parents=True, exist_ok=True)
    MARKET_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if matched.empty:
        return
    out_cols = [
        c
        for c in [
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
        if c in matched.columns
    ]
    matched[out_cols].to_csv(MARKET_EVAL_CSV, index=False)


def format_summary_table(results: dict) -> str:
    ll_model = results.get("log_loss_model")
    ll_market = results.get("log_loss_market")
    ll_model_s = f"{ll_model:.4f}" if ll_model is not None else "—"
    ll_market_s = f"{ll_market:.4f}" if ll_market is not None else "—"
    sources = ", ".join(results.get("odds_sources") or []) or "none"
    spread_ll = results.get("spread_cover_log_loss")
    totals_ll = results.get("totals_over_log_loss")
    return (
        f"Model: {results.get('model_version', 'unknown')}\n"
        f"Holdout season: {results.get('holdout_season')} "
        f"({results['holdout_games']} games)\n"
        f"Matched with odds: {results['matched_games']} "
        f"({results['match_rate_pct']}%) — ML: {results.get('matched_ml_games', 0)} "
        f"({results.get('ml_match_rate_pct', 0)}%)\n"
        f"Sources: {sources}\n"
        f"Log loss — model: {ll_model_s}, market: {ll_market_s}\n"
        f"Brier: {results.get('brier_model', '—')}  "
        f"Accuracy: {results.get('accuracy_model', '—')}\n"
        f"+EV picks (edge > {results['edge_threshold']}): {results['plus_ev_picks']}\n"
        f"Paper ROI (flat $1): {results['paper_trade_roi']:.2%} "
        f"({results['paper_trade_profit_units']} units)\n"
        f"Spread cover log loss: {spread_ll if spread_ll is not None else '—'}\n"
        f"Totals over log loss: {totals_ll if totals_ll is not None else '—'}\n"
        f"EV signal (ROI > 0): {results['ev_signal']}\n"
        f"Betting-ready: {results.get('betting_ready')} (forward CLV required)"
    )
