"""UFC daily board — moneyline model vs market for upcoming cards."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.ufc_baseline import ACTIVE_UFC_MANIFEST, load_model_artifact
from app.odds.live_odds import live_odds_enabled
from app.parlay.ufc_parlay import top_parlays_payload
from app.services.schedule_ufc import get_ufc_schedule
from app.services.ufc_forward_clv import LIVE_ODDS_SOURCES, log_live_picks
from app.services.ufc_slate_predictions import predict_slate_with_meta

logger = logging.getLogger(__name__)

UFC_DISCLAIMER = (
    "UFC logistic model with fighter Elo, form, and rest features — not betting advice."
)

DEMO_DATE = "2024-01-13"


def _active_ufc_model_info() -> dict[str, Any]:
    if ACTIVE_UFC_MANIFEST.exists():
        try:
            return json.loads(ACTIVE_UFC_MANIFEST.read_text(encoding="utf-8"))
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
    schedule_fights: list[dict[str, Any]],
    preds: dict[str, dict[str, Any]],
    *,
    min_edge: float,
    card_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fight in schedule_fights:
        fid = str(fight.get("fight_id") or fight.get("game_id") or "")
        pred = preds.get(fid) or {}
        home = pred.get("home_team") or fight.get("home_team")
        away = pred.get("away_team") or fight.get("away_team")
        matchup = f"{home} vs {away}"
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
                        "fighter": home,
                        "edge": round(float(edge_home), 4),
                        "american_odds": pred.get("home_ml"),
                    }
                elif float(edge_away) >= min_edge:
                    best_pick = {
                        "side": "away",
                        "fighter": away,
                        "edge": round(float(edge_away), 4),
                        "american_odds": pred.get("away_ml"),
                    }

        rows.append(
            {
                "fight_id": fid,
                "game_id": fid,
                "date": card_date,
                "matchup": matchup,
                "away_team": away,
                "home_team": home,
                "event_name": fight.get("event_name") or pred.get("event_name"),
                "weight_class": fight.get("weight_class") or pred.get("weight_class"),
                "start_time_utc": fight.get("start_time_utc"),
                "status": fight.get("status"),
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
                "totals_line": pred.get("totals_line"),
                "over_odds": pred.get("over_odds"),
                "under_odds": pred.get("under_odds"),
                "method_props": pred.get("method_props"),
                "goes_distance_yes": pred.get("goes_distance_yes"),
                "goes_distance_no": pred.get("goes_distance_no"),
            }
        )
    return rows


def build_ufc_daily_board(
    game_date: date | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = 5,
    use_cache: bool = False,
    force_refresh: bool = False,
    log_clv: bool = True,
) -> dict[str, Any]:
    if use_cache and game_date is None:
        game_date = date.fromisoformat(DEMO_DATE)
    game_date = game_date or date.today()
    mode = "demo" if use_cache else "live"
    warnings: list[str] = []

    schedule = get_ufc_schedule(game_date, auto_resolve=not use_cache)
    fights = list(schedule.get("games") or [])
    resolved = schedule.get("resolved_date") or schedule.get("date") or game_date.isoformat()
    slate_day = date.fromisoformat(str(resolved)[:10])

    base: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": slate_day.isoformat(),
        "sport": "ufc",
        "mode": mode,
        "disclaimer": UFC_DISCLAIMER,
        "warnings": warnings,
        "message": None,
        "odds_source": "none",
        "edge_threshold": min_edge,
        "betting_ready": False,
        "active_moneyline_model": _active_ufc_model_info(),
        "slate": [],
        "plus_ev_count": 0,
        "top_parlays": [],
        "events": schedule.get("events") or [],
    }

    if not fights:
        base["message"] = "No UFC fights scheduled for this date."
        return base

    try:
        preds, odds_source = predict_slate_with_meta(
            slate_day,
            force_refresh=force_refresh and not use_cache,
        )
    except FileNotFoundError as exc:
        base["error"] = str(exc)
        base["message"] = (
            "UFC model not trained — run scripts/bootstrap_ufc.py "
            "(or scripts/ingest_ufc.py then train)."
        )
        return base

    if odds_source and odds_source != "none":
        base["odds_source"] = odds_source

    if not preds:
        base["message"] = "No model predictions available for this card."
        warnings.append("Train models with scripts/bootstrap_ufc.py if this persists.")

    slate = _slate_rows(fights, preds, min_edge=min_edge, card_date=slate_day.isoformat())
    has_ml_odds = any(g.get("home_ml") is not None for g in slate)
    if not has_ml_odds:
        warnings.append("Moneyline odds unavailable — showing model probabilities only.")

    if use_cache:
        base["message"] = f"Demo board — {slate_day.isoformat()} UFC card."

    base["slate"] = slate
    base["plus_ev_count"] = sum(1 for g in slate if g.get("plus_ev_single"))
    base["fights_with_odds"] = sum(1 for g in slate if g.get("home_ml") is not None)
    base["top_parlays"] = (
        top_parlays_payload(slate, min_edge=min_edge, max_parlays=max_parlays)
        if has_ml_odds
        else []
    )
    base["warnings"] = warnings

    if (
        not use_cache
        and log_clv
        and live_odds_enabled()
        and base.get("odds_source") in LIVE_ODDS_SOURCES
        and base.get("plus_ev_count", 0) > 0
    ):
        try:
            logged = log_live_picks(base)
            base["clv_logged"] = logged
            base["clv_logged_count"] = len(logged)
        except Exception as exc:
            logger.warning("UFC forward CLV log skipped: %s", exc)
            base["clv_log_error"] = str(exc)

    return base
