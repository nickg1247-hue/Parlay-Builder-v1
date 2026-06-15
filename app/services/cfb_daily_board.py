"""CFB daily board — display-only analytics slate (NBA board pattern)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.models.cfb_baseline import ACTIVE_CFB_MANIFEST, load_model_artifact
from app.models.constants import DEFAULT_MIN_EDGE
from app.services.cfb_slate_predictions import cfb_season_end_year, predict_slate
from app.services.cfb_team_logos import enrich_games_logos
from app.services.schedule_cfb import get_cfb_schedule

CFB_DISCLAIMER = (
    "CFB logistic model with Elo, form, conference, and SP+ features — not betting advice. "
    "Spread/totals use proxy lines when sportsbook data is unavailable."
)

DEMO_DATE = "2024-11-30"


def _active_cfb_model_info() -> dict[str, Any]:
    if ACTIVE_CFB_MANIFEST.exists():
        try:
            return json.loads(ACTIVE_CFB_MANIFEST.read_text(encoding="utf-8"))
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


def _slate_rows(
    schedule_games: list[dict[str, Any]],
    preds: dict[str, dict[str, Any]],
    *,
    min_edge: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in schedule_games:
        gid = str(game.get("game_id") or "")
        pred = preds.get(gid) or {}
        home = pred.get("home_team") or game.get("home_team")
        away = pred.get("away_team") or game.get("away_team")
        matchup = f"{away} @ {home}"
        prob_home = pred.get("model_prob_home")
        prob_away = pred.get("model_prob_away")
        if prob_home is not None and prob_away is None:
            prob_away = round(1.0 - float(prob_home), 4)

        edge_home = pred.get("ev_home")
        edge_away = pred.get("ev_away")
        ml_edge_best = pred.get("model_edge_ml")
        if edge_home is not None and edge_away is not None:
            ml_edge_best = max(float(edge_home), float(edge_away))

        best_pick = None
        plus_ev = bool(pred.get("plus_ev_ml"))
        if plus_ev:
            if edge_home is not None and edge_away is not None:
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

        row: dict[str, Any] = {
            "game_id": gid,
            "matchup": matchup,
            "away_team": away,
            "home_team": home,
            "away_logo_url": game.get("away_logo_url"),
            "home_logo_url": game.get("home_logo_url"),
            "start_time_utc": game.get("start_time_utc"),
            "status": game.get("status"),
            "model_prob_home": prob_home,
            "model_prob_away": prob_away,
            "model_pick": pred.get("model_pick"),
            "model_pick_side": pred.get("model_pick_side"),
            "market_prob_home": pred.get("market_prob_home"),
            "market_prob_away": pred.get("market_prob_away"),
            "edge_home": edge_home,
            "edge_away": edge_away,
            "ml_edge_best": ml_edge_best,
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
        rows.append(row)
    return rows


def build_cfb_daily_board(
    game_date: date | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    use_cache: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Build CFB slate envelope for /cfb/board UI."""
    del force_refresh  # predict_slate uses CFBD lines cache + optional live odds
    if use_cache and game_date is None:
        game_date = date.fromisoformat(DEMO_DATE)
    game_date = game_date or date.today()
    mode = "demo" if use_cache else "live"
    warnings: list[str] = []

    schedule = get_cfb_schedule(game_date, auto_resolve=not use_cache)
    games = list(schedule.get("games") or [])
    enrich_games_logos(games)

    resolved = schedule.get("resolved_date") or schedule.get("date") or game_date.isoformat()
    slate_day = date.fromisoformat(str(resolved)[:10])

    base: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": slate_day.isoformat(),
        "sport": "cfb",
        "mode": mode,
        "disclaimer": CFB_DISCLAIMER,
        "warnings": warnings,
        "message": None,
        "odds_source": "cfbd_lines",
        "edge_threshold": min_edge,
        "betting_ready": False,
        "active_moneyline_model": _active_cfb_model_info(),
        "slate": [],
        "plus_ev_count": 0,
        "board_spread_enabled": True,
        "board_totals_enabled": True,
    }

    if not games:
        base["message"] = "No CFB games scheduled for this date."
        return base

    try:
        preds = predict_slate(slate_day)
    except FileNotFoundError as exc:
        base["error"] = str(exc)
        base["message"] = (
            "CFB model not trained — run scripts/bootstrap_cfb.py "
            "(or scripts/train_cfb_baseline.py after ingest)."
        )
        return base

    if not preds:
        base["message"] = "No model predictions available for this slate."
        warnings.append("Train models with scripts/bootstrap_cfb.py if this persists.")

    slate = _slate_rows(games, preds, min_edge=min_edge)
    has_ml_odds = any(g.get("home_ml") is not None for g in slate)
    has_spread = any(g.get("home_spread_point") is not None for g in slate)
    has_totals = any(g.get("ou_line") is not None for g in slate)

    if not has_ml_odds:
        warnings.append("Moneyline odds unavailable — showing model probabilities only.")
    if not has_spread:
        warnings.append("Spread lines may use proxy (-7) when CFBD cache is empty.")
    if not has_totals:
        warnings.append("O/U lines may be missing for some games.")

    if use_cache:
        base["message"] = (
            f"Demo board — {slate_day.isoformat()} rivalry slate with CFBD cached lines."
        )

    base["slate"] = slate
    base["plus_ev_count"] = sum(1 for g in slate if g.get("plus_ev_single"))
    base["games_with_odds"] = sum(1 for g in slate if g.get("home_ml") is not None)
    base["warnings"] = warnings
    return base
