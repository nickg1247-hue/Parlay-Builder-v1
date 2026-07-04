"""Lightweight home-page summary from morning board cache."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.odds_repository import get_today_snapshot
from app.services.bet_context import enrich_ml_singles, form_composite_score
from app.services.prop_scoring import prop_form_average_from_prop
from app.services.daily_board import DAILY_BOARD_CACHE, _top_form_singles
from app.services.ufc_home_summary import get_ufc_home_chip

logger = logging.getLogger(__name__)

STATIC_COLORS = PROJECT_ROOT / "static" / "mlb_team_colors.json"
ML_BET_FORM_FLOOR = 0.72


def _load_board() -> dict[str, Any] | None:
    if not DAILY_BOARD_CACHE.exists():
        return None
    try:
        return json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read daily board for home summary: %s", exc)
        return None


def _slate_index(slate: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in slate:
        gid = str(row.get("game_id", ""))
        if not gid:
            continue
        best = row.get("best_pick")
        out[gid] = {
            "game_id": gid,
            "matchup": row.get("matchup"),
            "away_team": row.get("away_team"),
            "home_team": row.get("home_team"),
            "best_pick": best,
            "model_pick_team": row.get("model_pick_team"),
            "model_pick_side": row.get("model_pick_side"),
            "model_confidence": row.get("model_confidence"),
            "ev_pick_team": row.get("ev_pick_team"),
            "ev_pick_edge": row.get("ev_pick_edge"),
            "totals_pick": row.get("totals_pick"),
            "ml_confidence": row.get("ml_confidence"),
            "totals_confidence": row.get("totals_confidence"),
            "expected_total_runs": row.get("expected_total_runs"),
            "ou_line": row.get("ou_line"),
            "plus_ev_single": row.get("plus_ev_single", False),
            "plus_ev_total": row.get("plus_ev_total", False),
            "model_prob_home": row.get("model_prob_home"),
            "home_ml": row.get("home_ml"),
            "away_ml": row.get("away_ml"),
            "home_starting_pitcher": row.get("home_starting_pitcher"),
            "away_starting_pitcher": row.get("away_starting_pitcher"),
            "home_pitcher_era": row.get("home_pitcher_era"),
            "away_pitcher_era": row.get("away_pitcher_era"),
        }
    return out


def _prop_form_composite(prop: dict[str, Any]) -> float:
    return prop_form_average_from_prop(prop)


def _dedupe_props(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for prop in props:
        key = (
            prop.get("player"),
            prop.get("market_type"),
            prop.get("line"),
            prop.get("recommended_side"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(prop)
    return out


def _best_bets_for_home(
    game_date: date,
    slate: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Top home picks: player props by form; ML only if very hot and beats props."""
    from app.services.props_mlb import build_daily_top_props

    try:
        props_payload = build_daily_top_props(game_date, limit=40, scan=False)
    except Exception as exc:
        logger.warning("Could not load daily props for home best bets: %s", exc)
        props_payload = {}

    raw_props = _dedupe_props(
        (props_payload.get("very_strong_props") or [])
        + (props_payload.get("top_props") or [])
    )
    prop_rows: list[dict[str, Any]] = []
    for prop in raw_props:
        if not prop.get("recommended_side"):
            continue
        prop_rows.append(
            {
                **prop,
                "bet_type": "prop",
                "form_score": round(_prop_form_composite(prop), 4),
            }
        )
    prop_rows.sort(key=lambda row: row["form_score"], reverse=True)

    ml_rows: list[dict[str, Any]] = []
    if slate:
        for pick in _top_form_singles(slate, game_date, limit=12):
            ml_rows.append(
                {
                    **pick,
                    "bet_type": "ml",
                    "form_score": round(form_composite_score(pick), 4),
                }
            )

    chosen = list(prop_rows[:limit])
    if len(chosen) < limit and ml_rows:
        floor = min((row["form_score"] for row in chosen), default=0.0)
        for ml in ml_rows:
            if len(chosen) >= limit:
                break
            if ml["form_score"] >= ML_BET_FORM_FLOOR and ml["form_score"] > floor:
                chosen.append(ml)
                chosen.sort(key=lambda row: row["form_score"], reverse=True)
                chosen = chosen[:limit]
                floor = chosen[-1]["form_score"] if chosen else 0.0

    if not chosen and ml_rows:
        chosen = ml_rows[:limit]

    return chosen[:limit]


def get_home_today_summary(game_date: date | None = None) -> dict[str, Any]:
    """Today at a glance + best bets from on-disk daily board (no rebuild)."""
    game_date = game_date or date.today()
    board = _load_board()
    odds_snap = get_today_snapshot()

    empty: dict[str, Any] = {
        "date": game_date.isoformat(),
        "board_available": False,
        "games_on_slate": 0,
        "games_with_odds": 0,
        "plus_ev_singles": 0,
        "plus_ev_totals": 0,
        "top_singles": [],
        "slate_by_game_id": {},
        "odds_fetched_at": odds_snap.get("fetched_at"),
        "odds_source": board.get("odds_source") if board else None,
        "message": "Run morning refresh or board Run live to populate picks.",
        "ufc_card": get_ufc_home_chip(),
    }

    if board is None or board.get("date") != game_date.isoformat():
        return empty

    slate = board.get("slate") or []
    plus_ev_singles = sum(1 for g in slate if g.get("plus_ev_single"))
    plus_ev_totals = sum(1 for g in slate if g.get("plus_ev_total"))
    top_singles = _best_bets_for_home(game_date, slate) if slate else []
    if top_singles and any(
        row.get("bet_type") == "ml"
        and (row.get("line_strength") is None or row.get("win_rate_l10") is None)
        for row in top_singles
    ):
        ml_only = [row for row in top_singles if row.get("bet_type") == "ml"]
        if ml_only:
            enriched = enrich_ml_singles(ml_only, slate, game_date)
            by_team = {row.get("team"): row for row in enriched}
            top_singles = [
                by_team.get(row.get("team"), row) if row.get("bet_type") == "ml" else row
                for row in top_singles
            ]

    return {
        "date": board.get("date", game_date.isoformat()),
        "board_available": True,
        "generated_at": board.get("generated_at"),
        "games_on_slate": board.get("games_on_slate", len(slate)),
        "games_with_odds": board.get("games_with_odds", 0),
        "plus_ev_singles": plus_ev_singles,
        "plus_ev_totals": plus_ev_totals,
        "top_singles": top_singles,
        "slate_by_game_id": _slate_index(slate),
        "odds_fetched_at": odds_snap.get("fetched_at"),
        "odds_source": board.get("odds_source"),
        "message": None,
        "ufc_card": get_ufc_home_chip(),
    }
