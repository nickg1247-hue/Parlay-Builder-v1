"""Server-side payloads for MLB HTML pages (no client GET /api)."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from app.odds.odds_repository import get_today_snapshot
from app.services.forward_clv import summarize_clv as summarize_mlb_clv
from app.services.game_insights import build_game_insights
from app.services.home_summary import get_home_today_summary
from app.services.morning_refresh import get_refresh_status
from app.services.performance_charts import model_vs_market_chart, performance_trend_chart
from app.services.prop_pick_tracker import summarize_prop_tracker
from app.services.props_mlb import (
    DEFAULT_DISPLAY_BOOKMAKER,
    build_daily_top_props,
    build_game_props,
    list_prop_bookmakers,
    list_prop_market_types,
    search_daily_props,
)
from app.services.schedule_mlb import get_mlb_schedule
from app.services.scores_today import get_scores_today

BUILD_PATH = Path(__file__).resolve().parents[2] / "BUILD"


def _read_build_id() -> str:
    if BUILD_PATH.exists():
        return BUILD_PATH.read_text(encoding="utf-8").strip()
    return "unknown"


def performance_summary_payload(days: int = 30) -> dict[str, Any]:
    summary = summarize_prop_tracker(days=days)
    clv = summarize_mlb_clv(days=days)
    return {
        "prop_tracker": summary,
        "clv": clv,
        "charts": {
            "model_vs_market": model_vs_market_chart(),
            "performance_trend": performance_trend_chart(days=days),
        },
    }


def build_home_page_data(game_date: date | None = None) -> dict[str, Any]:
    game_date = game_date or date.today()
    summary = get_home_today_summary(game_date)
    scores = get_scores_today(sport="all", game_date=game_date, auto_resolve=False)
    odds = get_today_snapshot()
    status = get_refresh_status()
    props_data = build_daily_top_props(
        game_date,
        limit=12,
        scan=False,
        refresh=False,
        cache_only=True,
    )
    return {
        "kind": "home",
        "date": game_date.isoformat(),
        "summary": summary,
        "scores": scores,
        "odds": odds,
        "status": status,
        "propsData": props_data,
        "trackerSummary": summarize_prop_tracker(days=30),
        "perfSummary": performance_summary_payload(days=30),
        "tickerScores": scores,
        "build": {"build_id": _read_build_id()},
    }


def build_mlb_slate_page_data(game_date: date | None = None) -> dict[str, Any]:
    game_date = game_date or date.today()
    slate = get_mlb_schedule(game_date)
    summary = get_home_today_summary(game_date)
    odds = get_today_snapshot()
    status = get_refresh_status()
    ticker = get_scores_today(sport="all", game_date=game_date, auto_resolve=False)
    return {
        "kind": "mlb_slate",
        "date": game_date.isoformat(),
        "slate": slate,
        "summary": summary,
        "odds": odds,
        "status": status,
        "tickerScores": ticker,
    }


async def build_mlb_game_page_data(
    game_id: str,
    game_date: date | None = None,
    *,
    use_cache: bool = False,
    refresh: bool = False,
    bookmaker: str | None = None,
) -> dict[str, Any] | None:
    game_date = game_date or date.today()
    book = bookmaker or DEFAULT_DISPLAY_BOOKMAKER
    insights = await asyncio.to_thread(
        build_game_insights,
        game_id,
        game_date=game_date,
        use_cache=use_cache,
        refresh=refresh,
    )
    if insights is None:
        return None
    props_payload = await asyncio.to_thread(
        build_game_props,
        game_id,
        game_date=game_date,
        refresh=refresh,
        bookmaker=book,
    )
    ticker = get_scores_today(sport="mlb", game_date=game_date, auto_resolve=False)
    return {
        "kind": "mlb_game",
        "gameId": game_id,
        "date": game_date.isoformat(),
        "bookmaker": book,
        "insights": insights,
        "gameProps": props_payload,
        "propMarkets": list_prop_market_types(),
        "bookmakers": list_prop_bookmakers(),
        "tickerScores": ticker,
        "odds": get_today_snapshot(),
    }


def build_mlb_props_page_data(
    game_date: date | None = None,
    *,
    bookmaker: str | None = None,
    market_type: str | None = None,
    min_odds: int | None = None,
    line_kind: str | None = None,
    line_value: float | None = None,
    side: str | None = None,
    actionable_only: bool = False,
    very_strong_only: bool = False,
    include_alternates: bool = False,
    sort: str = "score",
    risk: str | None = None,
    min_score: int | None = None,
    min_hit_l5: float | None = None,
    min_hit_l10: float | None = None,
    limit: int = 200,
    scan: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    game_date = game_date or date.today()
    if refresh:
        scan = True
    search_kwargs: dict[str, Any] = dict(
        bookmaker=bookmaker,
        market_type=market_type,
        min_odds=min_odds,
        line_kind=line_kind,
        line_value=line_value,
        side=side,
        actionable_only=actionable_only,
        limit=limit,
        scan=scan,
        refresh=refresh,
        include_alternates=include_alternates,
        very_strong_only=very_strong_only,
        sort=sort,
        risk=risk,
        min_score=min_score,
        min_hit_l5=min_hit_l5,
        min_hit_l10=min_hit_l10,
    )
    props_search = search_daily_props(game_date, **search_kwargs)
    if not props_search.get("props") and not scan and not refresh:
        props_search = search_daily_props(
            game_date,
            **{**search_kwargs, "scan": True, "refresh": False},
        )
        props_search["auto_scanned"] = True
    filters = {
        "bookmaker": bookmaker or DEFAULT_DISPLAY_BOOKMAKER,
        "market_type": market_type or "",
        "min_odds": min_odds,
        "line_kind": line_kind or "main",
        "line_value": line_value,
        "side": side or "both",
        "actionable_only": actionable_only,
        "very_strong_only": very_strong_only,
        "include_alternates": include_alternates,
        "sort": sort or "score",
        "risk": risk or "",
        "min_score": min_score,
        "min_hit_l5": min_hit_l5,
        "min_hit_l10": min_hit_l10,
    }
    return {
        "kind": "mlb_props",
        "date": game_date.isoformat(),
        "propsSearch": props_search,
        "markets": list_prop_market_types(),
        "bookmakers": list_prop_bookmakers(),
        "tracker": summarize_prop_tracker(days=30),
        "filters": filters,
        "status": get_refresh_status(),
        "tickerScores": get_scores_today(sport="all", game_date=game_date, auto_resolve=False),
    }
