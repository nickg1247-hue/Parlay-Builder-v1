"""NFL weekly board — slate analytics + +EV singles."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nfl_baseline import ACTIVE_NFL_MANIFEST, load_model_artifact
from app.parlay.nfl_parlay import top_parlays_payload
from app.services.nfl_slate_predictions import predict_slate
from app.services.slate_clock import slate_today
from app.services.schedule_nfl import get_nfl_schedule

NFL_DISCLAIMER = (
    "NFL moneyline v2 (GBR + Elo toss-up) with toss-up / soft / hard / lock "
    "labels from 2024–2025 regular-season hit rates. Not betting advice. "
    "Preseason cannot be hard or lock. Betting_ready=false until forward CLV validates edge."
)
DEMO_DATE = "2025-09-07"


def _active_nfl_model_info() -> dict[str, Any]:
    if ACTIVE_NFL_MANIFEST.exists():
        try:
            return json.loads(ACTIVE_NFL_MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    try:
        artifact = load_model_artifact()
        return {
            "model_version": artifact.get("model_version", "unknown"),
            "feature_set": artifact.get("feature_set"),
        }
    except FileNotFoundError:
        return {}


def _slate_rows(schedule_games, preds, *, min_edge: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in schedule_games:
        gid = str(game.get("game_id") or "")
        pred = preds.get(gid) or {}
        home = pred.get("home_team") or game.get("home_team")
        away = pred.get("away_team") or game.get("away_team")
        edge_home = pred.get("ev_home")
        edge_away = pred.get("ev_away")
        plus_ev = bool(pred.get("plus_ev_ml"))
        best_pick = None
        if plus_ev and edge_home is not None and edge_away is not None:
            if float(edge_home) >= float(edge_away) and float(edge_home) >= min_edge:
                best_pick = {
                    "side": "home",
                    "team": home,
                    "edge": round(float(edge_home), 4),
                    "american_odds": pred.get("home_ml"),
                }
            elif float(edge_away) >= min_edge:
                best_pick = {
                    "side": "away",
                    "team": away,
                    "edge": round(float(edge_away), 4),
                    "american_odds": pred.get("away_ml"),
                }
        rows.append(
            {
                "game_id": gid,
                "matchup": f"{away} @ {home}",
                "away_team": away,
                "home_team": home,
                "away_logo_url": game.get("away_logo_url"),
                "home_logo_url": game.get("home_logo_url"),
                "start_time_utc": game.get("start_time_utc"),
                "status": game.get("status"),
                "game_type": pred.get("game_type") or game.get("game_type") or "regular",
                "model_prob_home": pred.get("model_prob_home"),
                "model_prob_away": pred.get("model_prob_away"),
                "model_pick": pred.get("model_pick"),
                "model_pick_side": pred.get("model_pick_side"),
                "model_category": pred.get("model_category"),
                "model_category_label": pred.get("model_category_label"),
                "model_confidence": pred.get("model_confidence") or pred.get("model_category_label"),
                "market_prob_home": pred.get("market_prob_home"),
                "market_prob_away": pred.get("market_prob_away"),
                "edge_home": edge_home,
                "edge_away": edge_away,
                "ml_edge_best": pred.get("model_edge_ml"),
                "ml_confidence": pred.get("ml_confidence"),
                "plus_ev_single": plus_ev,
                "best_pick": best_pick,
                "home_ml": pred.get("home_ml"),
                "away_ml": pred.get("away_ml"),
                "model_margin": pred.get("model_margin"),
                "spread_pick": pred.get("spread_pick"),
                "home_spread_point": pred.get("home_spread_point"),
                "spread_line_source": pred.get("spread_line_source"),
                "spread_confidence": pred.get("spread_confidence"),
                "expected_total_pts": pred.get("expected_total_pts"),
                "totals_pick": pred.get("totals_pick"),
                "ou_line": pred.get("ou_line"),
                "ou_line_source": pred.get("ou_line_source"),
                "totals_confidence": pred.get("totals_confidence"),
                "model_prob_over": pred.get("model_prob_over"),
            }
        )
    return rows


def build_nfl_daily_board(
    game_date: date | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    use_cache: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    del force_refresh
    if use_cache and game_date is None:
        game_date = date.fromisoformat(DEMO_DATE)
    game_date = game_date or slate_today()
    mode = "demo" if use_cache else "live"
    warnings: list[str] = []
    schedule = get_nfl_schedule(game_date, auto_resolve=not use_cache)
    games = list(schedule.get("games") or [])
    resolved = schedule.get("resolved_date") or schedule.get("date") or game_date.isoformat()
    slate_day = date.fromisoformat(str(resolved)[:10])
    base: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": slate_day.isoformat(),
        "sport": "nfl",
        "mode": mode,
        "disclaimer": NFL_DISCLAIMER,
        "warnings": warnings,
        "message": None,
        "odds_source": "none",
        "edge_threshold": min_edge,
        "betting_ready": False,
        "active_moneyline_model": _active_nfl_model_info(),
        "slate": [],
        "parlays": [],
        "plus_ev_count": 0,
    }
    if not games:
        base["message"] = "No NFL games scheduled for this date."
        return base
    try:
        preds = predict_slate(slate_day)
    except FileNotFoundError as exc:
        base["error"] = str(exc)
        base["message"] = "NFL model not trained — run scripts/bootstrap_nfl.py."
        return base
    slate = _slate_rows(games, preds, min_edge=min_edge)
    if not any(g.get("home_ml") is not None for g in slate):
        warnings.append("Moneyline odds unavailable — showing model probabilities only.")
    first_pred = next(iter(preds.values()), {})
    base["odds_source"] = first_pred.get("odds_source") or "none"
    base["slate"] = slate
    base["plus_ev_count"] = sum(1 for g in slate if g.get("plus_ev_single"))
    base["parlays"] = top_parlays_payload(slate, min_edge=min_edge)
    base["warnings"] = warnings
    if use_cache:
        base["message"] = f"Demo board — {slate_day.isoformat()}."
    return base
