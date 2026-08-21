"""Shared player-props platform: sport dispatch over MLB/NFL implementations."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.mlb_page_data import build_mlb_props_page_data
from app.services.morning_refresh import get_refresh_status
from app.services.prop_books import DEFAULT_DISPLAY_BOOKMAKER, normalize_prop_sport
from app.services.prop_engine.nfl_markets import list_nfl_market_types
from app.services.prop_pick_tracker import summarize_prop_tracker
from app.services.props_mlb import (
    build_game_props as build_mlb_game_props,
    list_prop_bookmakers,
    list_prop_market_types,
    refresh_props_slate as refresh_mlb_props_slate,
    search_daily_props as search_mlb_daily_props,
)
from app.services.props_nfl import (
    build_nfl_game_props,
    list_nfl_bookmakers,
    refresh_nfl_props_slate,
    search_nfl_daily_props,
)

SUPPORTED_PROP_SPORTS = ("mlb", "nfl")


def list_prop_sports() -> list[dict[str, str]]:
    return [
        {"key": "mlb", "label": "MLB"},
        {"key": "nfl", "label": "NFL"},
    ]


def list_markets_for_sport(sport: str | None) -> list[dict[str, str]]:
    if normalize_prop_sport(sport) == "nfl":
        return list_nfl_market_types()
    return list_prop_market_types()


def list_bookmakers_for_sport(sport: str | None) -> list[dict[str, Any]]:
    if normalize_prop_sport(sport) == "nfl":
        return list_nfl_bookmakers()
    return list_prop_bookmakers()


def search_props(sport: str | None, game_date: date | None = None, **kwargs: Any) -> dict[str, Any]:
    key = normalize_prop_sport(sport)
    if key == "nfl":
        result = search_nfl_daily_props(game_date, **_nfl_search_kwargs(kwargs))
    else:
        result = search_mlb_daily_props(game_date, **_mlb_search_kwargs(kwargs))
    result["sport"] = key
    return result


def refresh_props(sport: str | None, game_date: date | None = None, **kwargs: Any) -> dict[str, Any]:
    key = normalize_prop_sport(sport)
    if key == "nfl":
        return refresh_nfl_props_slate(game_date, **kwargs)
    return refresh_mlb_props_slate(game_date, **kwargs)


def build_sport_game_props(
    sport: str | None,
    game_id: str,
    game_date: date | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    key = normalize_prop_sport(sport)
    if key == "nfl":
        return build_nfl_game_props(game_id, game_date, **kwargs)
    return build_mlb_game_props(game_id, game_date, **kwargs)


def _mlb_search_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "bookmaker",
        "market_type",
        "min_odds",
        "line_kind",
        "line_value",
        "side",
        "actionable_only",
        "very_strong_only",
        "include_alternates",
        "sort",
        "risk",
        "min_score",
        "min_hit_l5",
        "min_hit_l10",
        "limit",
        "scan",
        "refresh",
    }
    return {k: v for k, v in kwargs.items() if k in allowed}


def _nfl_search_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "bookmaker",
        "market_type",
        "min_odds",
        "line_kind",
        "line_value",
        "side",
        "actionable_only",
        "very_strong_only",
        "include_alternates",
        "sort",
        "risk",
        "min_score",
        "min_edge",
        "position",
        "team",
        "limit",
        "scan",
        "refresh",
        "min_hit_l5",
        "min_hit_l10",
    }
    return {k: v for k, v in kwargs.items() if k in allowed}


async def build_player_props_page_data(
    sport: str | None = None,
    game_date: date | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    key = normalize_prop_sport(sport)
    if key == "mlb":
        data = await build_mlb_props_page_data(game_date, **_mlb_search_kwargs(kwargs))
        data["kind"] = "player_props"
        data["sport"] = "mlb"
        data["sports"] = list_prop_sports()
        return data

    import asyncio

    game_date = game_date or date.today()
    search_kwargs = _nfl_search_kwargs(kwargs)
    props_search, markets, bookmakers, status = await asyncio.gather(
        asyncio.to_thread(search_nfl_daily_props, game_date, **search_kwargs),
        asyncio.to_thread(list_nfl_market_types),
        asyncio.to_thread(list_nfl_bookmakers),
        asyncio.to_thread(get_refresh_status),
    )
    try:
        tracker = await asyncio.to_thread(summarize_prop_tracker, 30)
    except Exception:
        tracker = {}
    filters = {
        "bookmaker": kwargs.get("bookmaker") or DEFAULT_DISPLAY_BOOKMAKER,
        "market_type": kwargs.get("market_type") or "",
        "min_odds": kwargs.get("min_odds"),
        "line_kind": kwargs.get("line_kind") or "main",
        "line_value": kwargs.get("line_value"),
        "side": kwargs.get("side") or "both",
        "actionable_only": bool(kwargs.get("actionable_only")),
        "very_strong_only": bool(kwargs.get("very_strong_only")),
        "include_alternates": bool(kwargs.get("include_alternates")),
        "sort": kwargs.get("sort") or "score",
        "risk": kwargs.get("risk") or "",
        "min_score": kwargs.get("min_score"),
        "position": kwargs.get("position") or "",
        "team": kwargs.get("team") or "",
        "min_edge": kwargs.get("min_edge"),
    }
    return {
        "kind": "player_props",
        "sport": "nfl",
        "sports": list_prop_sports(),
        "date": str(props_search.get("date") or (game_date.isoformat() if game_date else "")),
        "propsSearch": props_search,
        "markets": markets,
        "bookmakers": bookmakers,
        "tracker": tracker,
        "filters": filters,
        "status": status,
        "tickerScores": {},
    }
