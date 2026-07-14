"""NBA Summer League daily board — market-implied leans only (no regular-season model)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_summer_odds_repository import get_nba_summer_odds_for_date
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.services.schedule_nba_summer import get_nba_summer_schedule

logger = logging.getLogger(__name__)

NBA_SUMMER_BOARD_CACHE = PROJECT_ROOT / "data" / "processed" / "nba_summer_daily_board.json"
BOARD_CACHE_TTL_SECONDS = 5 * 60

DISCLAIMER = (
    "NBA Summer League — market-implied leans only. "
    "Regular-season NBA models are not applied (different rosters and pace). "
    "Experimental analytics — not betting advice."
)


def _has_odds_api_key() -> bool:
    return bool(os.getenv("ODDS_API_KEY", "").strip())


def _board_age_seconds(cached: dict[str, Any]) -> float:
    generated = cached.get("generated_at")
    if not generated:
        return float("inf")
    try:
        ts = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _attach_odds(
    slate_df: pd.DataFrame,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    merged = slate_df.copy()
    for col in (
        "home_ml",
        "away_ml",
        "home_spread_point",
        "home_spread_american",
        "away_spread_point",
        "away_spread_american",
        "ou_line",
        "over_odds",
        "under_odds",
    ):
        merged[col] = np.nan

    odds_games, source = get_nba_summer_odds_for_date(
        game_date,
        force_refresh=force_refresh,
        include_spreads=True,
        include_totals=True,
    )
    if not odds_games:
        return merged, "none" if source == "none" else source

    odds_by_matchup: dict[tuple[str, str], dict[str, Any]] = {}
    for og in odds_games:
        key = (
            normalize_nba_team_name(og.get("home_team", "")),
            normalize_nba_team_name(og.get("away_team", "")),
        )
        odds_by_matchup[key] = og

    for idx, row in merged.iterrows():
        key = (
            normalize_nba_team_name(row.get("home_team", "")),
            normalize_nba_team_name(row.get("away_team", "")),
        )
        og = odds_by_matchup.get(key)
        if not og:
            continue
        for col in (
            "home_ml",
            "away_ml",
            "home_spread_point",
            "home_spread_american",
            "away_spread_point",
            "away_spread_american",
            "ou_line",
            "over_odds",
            "under_odds",
        ):
            if og.get(col) is not None:
                merged.at[idx, col] = og[col]

    return merged, source or "the_odds_api_live"


def _slate_rows(merged: pd.DataFrame, odds_source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    has_odds = odds_source not in ("none", "model_only", "")
    for row in merged.itertuples(index=False):
        home = str(row.home_team)
        away = str(row.away_team)
        market_home = None
        market_away = None
        model_pick_side = None
        model_pick_team = None
        model_pick_prob = None
        edge_home = None

        home_ml = getattr(row, "home_ml", None)
        away_ml = getattr(row, "away_ml", None)
        if (
            has_odds
            and is_valid_american_odds(home_ml)
            and is_valid_american_odds(away_ml)
        ):
            market_home, market_away = market_probs_from_american(
                int(home_ml), int(away_ml)
            )
            if market_home >= market_away:
                model_pick_side = "home"
                model_pick_team = home
                model_pick_prob = market_home
            else:
                model_pick_side = "away"
                model_pick_team = away
                model_pick_prob = market_away

        rows.append(
            {
                "game_id": str(row.game_id),
                "matchup": f"{away} @ {home}",
                "away_team": away,
                "home_team": home,
                "start_time_utc": getattr(row, "start_time_utc", None),
                "status": getattr(row, "status", None),
                "summer_league": getattr(row, "summer_league", None),
                "series_summary": getattr(row, "series_summary", None),
                "home_logo_url": getattr(row, "home_logo_url", None),
                "away_logo_url": getattr(row, "away_logo_url", None),
                "home_team_abbr": getattr(row, "home_team_abbr", None),
                "away_team_abbr": getattr(row, "away_team_abbr", None),
                "model_prob_home": round(market_home, 4) if market_home is not None else None,
                "market_prob_home": round(market_home, 4) if market_home is not None else None,
                "display_prob_home": round(market_home, 4) if market_home is not None else None,
                "edge_home": edge_home,
                "home_ml": int(home_ml) if is_valid_american_odds(home_ml) else None,
                "away_ml": int(away_ml) if is_valid_american_odds(away_ml) else None,
                "home_spread_point": getattr(row, "home_spread_point", None)
                if pd.notna(getattr(row, "home_spread_point", None))
                else None,
                "away_spread_point": getattr(row, "away_spread_point", None)
                if pd.notna(getattr(row, "away_spread_point", None))
                else None,
                "ou_line": getattr(row, "ou_line", None)
                if pd.notna(getattr(row, "ou_line", None))
                else None,
                "over_odds": int(row.over_odds)
                if is_valid_american_odds(getattr(row, "over_odds", None))
                else None,
                "under_odds": int(row.under_odds)
                if is_valid_american_odds(getattr(row, "under_odds", None))
                else None,
                "model_pick_side": model_pick_side,
                "model_pick_team": model_pick_team,
                "model_pick_prob": round(model_pick_prob, 4)
                if model_pick_prob is not None
                else None,
                "model_confidence": "Market lean" if model_pick_team else "—",
                "model_pick_action": "lean_only" if model_pick_team else "none",
                "plus_ev_single": False,
                "pick_source": "market_implied",
            }
        )
    return rows


def build_nba_summer_daily_board(
    game_date: date | None = None,
    *,
    refresh: bool = False,
    odds_force_refresh: bool | None = None,
) -> dict[str, Any]:
    game_date = game_date or date.today()
    cache_key = f"{game_date.isoformat()}_nba_summer"

    if not refresh and NBA_SUMMER_BOARD_CACHE.exists():
        try:
            cached = json.loads(NBA_SUMMER_BOARD_CACHE.read_text(encoding="utf-8"))
            if (
                cached.get("cache_key") == cache_key
                and _board_age_seconds(cached) < BOARD_CACHE_TTL_SECONDS
            ):
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    warnings: list[str] = []
    schedule = get_nba_summer_schedule(game_date, auto_resolve=False)
    games = schedule.get("games") or []
    if not games:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_key": cache_key,
            "date": game_date.isoformat(),
            "sport": "nba-summer",
            "mode": "live",
            "disclaimer": DISCLAIMER,
            "warnings": ["No NBA Summer League games scheduled for this date."],
            "odds_source": "none",
            "slate": [],
            "games_on_slate": 0,
            "betting_ready": False,
            "pick_mode": "market_implied",
        }
        NBA_SUMMER_BOARD_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NBA_SUMMER_BOARD_CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    slate_df = pd.DataFrame(games)
    force_odds = refresh if odds_force_refresh is None else odds_force_refresh

    if not live_odds_enabled():
        if not _has_odds_api_key():
            warnings.append(
                "No ODDS_API_KEY — showing schedule only. "
                "Set USE_LIVE_ODDS=true and ODDS_API_KEY for Summer League lines."
            )
        else:
            warnings.append(
                "USE_LIVE_ODDS=false — schedule only, no Summer League sportsbook lines."
            )
        merged = slate_df
        odds_source = "none"
    else:
        merged, odds_source = _attach_odds(
            slate_df, game_date, force_refresh=force_odds
        )
        if odds_source in ("none",):
            warnings.append(
                "No Summer League odds matched this slate "
                "(Odds API sport: basketball_nba_summer_league)."
            )

    slate = _slate_rows(merged, odds_source if live_odds_enabled() else "none")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
        "date": game_date.isoformat(),
        "sport": "nba-summer",
        "mode": "live",
        "disclaimer": DISCLAIMER,
        "warnings": warnings,
        "odds_source": odds_source if live_odds_enabled() else "model_only",
        "slate": slate,
        "games_on_slate": len(slate),
        "games_with_odds": sum(1 for g in slate if g.get("home_ml") is not None),
        "betting_ready": False,
        "pick_mode": "market_implied",
        "note": (
            "Picks are sportsbook-implied favorites when lines are available — "
            "not the regular-season NBA weighted model."
        ),
    }
    NBA_SUMMER_BOARD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NBA_SUMMER_BOARD_CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
