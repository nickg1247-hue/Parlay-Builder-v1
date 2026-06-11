"""Rolling-window NBA backtest report (2025-26 holdout moneyline)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.features.nba_pregame import build_features_for_history
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nba_baseline import HOLDOUT_SEASON, load_games, load_model_artifact, predict_home_win_proba
from app.odds.nba_market_eval import _merge_games_odds, _pick_side
from app.odds.nba_odds_free import HOLDOUT_SEASON_END, load_holdout_odds
from app.odds.odds_math import american_payout_profit, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "nba_backtest_report.json"
DEFAULT_START = date(2026, 3, 25)
DEFAULT_END = date(2026, 4, 10)


def _empty_report(
    *,
    days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "holdout_season_label": "2025-26",
        "holdout_season_end": HOLDOUT_SEASON_END,
        "games_in_window": 0,
        "model": {
            "log_loss": None,
            "accuracy_pct": None,
            "winner_pick_rate_pct": None,
        },
        "market": {
            "games_with_odds": 0,
            "odds_sources": [],
            "log_loss_market": None,
            "log_loss_model": None,
            "model_beats_market": None,
            "plus_ev_picks": 0,
            "paper_trade_roi": 0.0,
            "paper_trade_profit_units": 0.0,
            "plus_ev_hit_rate": 0.0,
            "edge_threshold": DEFAULT_MIN_EDGE,
        },
        "edge_threshold": DEFAULT_MIN_EDGE,
        "betting_ready": False,
        "status": "error" if error else "ok",
        "error": error,
    }


def _resolve_window(
    completed: pd.DataFrame,
    *,
    days: int | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date, pd.DataFrame, int | None]:
    if completed.empty:
        raise ValueError("No completed holdout games")

    dates = pd.to_datetime(completed["date"])

    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        window = completed[
            (dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))
        ].copy()
        span_days = (end_date - start_date).days + 1
        return start_date, end_date, window, span_days

    eval_end_ts = dates.max()
    eval_end = eval_end_ts.date()
    span = days if days is not None else 14
    start_ts = eval_end_ts - timedelta(days=span)
    window = completed[(dates >= start_ts) & (dates <= eval_end_ts)].copy()
    return start_ts.date(), eval_end, window, span


def _model_metrics(window_feat: pd.DataFrame) -> dict[str, Any]:
    if window_feat.empty:
        return {
            "log_loss": None,
            "accuracy_pct": None,
            "winner_pick_rate_pct": None,
        }

    probs = predict_home_win_proba(window_feat)
    probs = np.clip(probs, 1e-6, 1 - 1e-6)
    y_true = window_feat["home_win"].astype(int).values
    ll = float(log_loss(y_true, probs))
    pick_home = probs >= 0.5
    acc = float((pick_home == (y_true == 1)).mean() * 100)
    return {
        "log_loss": round(ll, 4),
        "accuracy_pct": round(acc, 2),
        "winner_pick_rate_pct": round(acc, 2),
    }


def _market_metrics(
    window_feat: pd.DataFrame,
    window_dates: set[str],
    min_edge: float = DEFAULT_MIN_EDGE,
) -> tuple[dict[str, Any], str | None]:
    empty = {
        "games_with_odds": 0,
        "odds_sources": [],
        "log_loss_market": None,
        "log_loss_model": None,
        "model_beats_market": None,
        "plus_ev_picks": 0,
        "paper_trade_roi": 0.0,
        "paper_trade_profit_units": 0.0,
        "plus_ev_hit_rate": 0.0,
        "edge_threshold": min_edge,
    }

    odds = load_holdout_odds(window_dates)
    if odds.empty:
        return empty, "No holdout odds in window (import nba_odds_2026.csv or repository snapshots)"

    odds_sources = sorted({str(s) for s in odds.get("odds_source", pd.Series(["csv_or_repository"])).dropna().unique()})

    matched = _merge_games_odds(window_feat, odds)
    valid = matched.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    matched = matched[valid].copy()
    if matched.empty:
        return {**empty, "odds_sources": odds_sources}, None

    artifact = load_model_artifact()
    cols = artifact["feature_columns"]
    matched = matched.copy()
    matched["model_prob_home"] = artifact["model"].predict_proba(matched[cols].values)[:, 1]
    matched["model_prob_away"] = 1.0 - matched["model_prob_home"]

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
    matched["pick_side"] = matched.apply(lambda r: _pick_side(r, min_edge), axis=1)

    y_true = matched["home_win"].astype(int).values
    model_ll = float(
        log_loss(y_true, np.clip(matched["model_prob_home"], 1e-6, 1 - 1e-6))
    )
    market_ll = float(
        log_loss(y_true, np.clip(matched["market_prob_home"], 1e-6, 1 - 1e-6))
    )

    plus_ev = matched[matched["pick_side"].notna()]
    profits: list[float] = []
    wins: list[int] = []
    for row in plus_ev.itertuples(index=False):
        if row.pick_side == "home":
            won = int(row.home_win) == 1
            profits.append(american_payout_profit(row.home_ml, won))
            wins.append(int(won))
        else:
            won = int(row.home_win) == 0
            profits.append(american_payout_profit(row.away_ml, won))
            wins.append(int(won))

    n_bets = len(plus_ev)
    total_profit = float(sum(profits)) if profits else 0.0
    roi = total_profit / n_bets if n_bets else 0.0
    hit_rate = float(np.mean(wins)) if wins else 0.0

    return {
        "games_with_odds": len(matched),
        "odds_sources": odds_sources,
        "log_loss_market": round(market_ll, 4),
        "log_loss_model": round(model_ll, 4),
        "model_beats_market": model_ll < market_ll,
        "plus_ev_picks": n_bets,
        "paper_trade_roi": round(roi, 4),
        "paper_trade_profit_units": round(total_profit, 2),
        "plus_ev_hit_rate": round(hit_rate, 4),
        "edge_threshold": min_edge,
    }, None


def run_nba_backtest_report(
    *,
    days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    write_cache: bool = True,
) -> dict[str, Any]:
    """
    Replay holdout games in a date window: model calibration + optional market paper trade.

    Use ``days`` (last N from latest completed holdout) **or** explicit ``start_date``/``end_date``.
    """
    try:
        games = load_games()
    except FileNotFoundError as exc:
        return _empty_report(days=days, start_date=start_date, end_date=end_date, error=str(exc))

    holdout = games[games["season"] == HOLDOUT_SEASON].copy()
    completed = holdout[holdout["home_win"].notna()].copy()
    if completed.empty:
        return _empty_report(
            days=days,
            start_date=start_date,
            end_date=end_date,
            error=f"No completed games for holdout season {HOLDOUT_SEASON}",
        )

    try:
        win_start, win_end, window, span_days = _resolve_window(
            completed,
            days=days,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        return _empty_report(days=days, start_date=start_date, end_date=end_date, error=str(exc))

    if window.empty:
        return _empty_report(
            days=span_days,
            start_date=win_start,
            end_date=win_end,
            error="No completed games in requested window",
        )

    feat = build_features_for_history(games)
    win_ids = set(window["game_id"].astype(str))
    window_feat = feat[feat["game_id"].astype(str).isin(win_ids)].copy()

    try:
        model_block = _model_metrics(window_feat)
    except FileNotFoundError as exc:
        return _empty_report(
            days=span_days,
            start_date=win_start,
            end_date=win_end,
            error=str(exc),
        )

    window_dates = set(pd.to_datetime(window["date"]).dt.strftime("%Y-%m-%d"))
    market_block, market_note = _market_metrics(window_feat, window_dates, min_edge=min_edge)

    errors: list[str] = []
    if market_note:
        errors.append(market_note)

    try:
        artifact = load_model_artifact()
        model_version = artifact.get("model_version", "unknown")
    except FileNotFoundError:
        model_version = "unknown"

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": span_days,
        "start_date": win_start.isoformat(),
        "end_date": win_end.isoformat(),
        "holdout_season_label": "2025-26",
        "holdout_season_end": HOLDOUT_SEASON_END,
        "model_version": model_version,
        "games_in_window": len(window),
        "model": model_block,
        "market": market_block,
        "edge_threshold": min_edge,
        "betting_ready": False,
        "status": "ok",
        "error": "; ".join(errors) if errors else None,
    }

    if write_cache:
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def load_saved_nba_backtest_report() -> dict[str, Any]:
    if not REPORT_JSON.exists():
        return _empty_report(error="No saved report. Run scripts/backtest_nba_recent.py.")
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))
