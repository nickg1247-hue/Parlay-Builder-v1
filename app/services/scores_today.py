"""Unified today's scores for MLB, NBA, CFB, and multi-sport ticker (Phase D)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.scores_cfb import get_cfb_scores_today
from app.services.scores_mlb import get_scores_today as get_mlb_scores_today
from app.services.scores_nba import get_nba_scores_today
from app.services.scores_ufc import get_ufc_scores_today

SUPPORTED_SPORTS = ("mlb", "nba", "cfb", "ufc", "all")


def _tag_sport(games: list[dict[str, Any]], sport: str) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for game in games:
        row = dict(game)
        row.setdefault("sport", sport)
        tagged.append(row)
    return tagged


def get_scores_today(
    sport: str = "mlb",
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
) -> dict[str, Any]:
    sport = sport.lower()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport: {sport}")

    if sport == "mlb":
        effective_date = game_date or date.today()
        payload = get_mlb_scores_today(game_date=effective_date)
        payload["games"] = _tag_sport(payload.get("games") or [], "mlb")
        return payload

    if sport == "nba":
        return get_nba_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
        )

    if sport == "cfb":
        return get_cfb_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
            force_live=game_date is None,
        )

    if sport == "ufc":
        return get_ufc_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
            force_live=game_date is None,
        )

    mlb_date = game_date or date.today()
    mlb = get_mlb_scores_today(game_date=mlb_date)
    nba = get_nba_scores_today(
        game_date=game_date,
        auto_resolve=auto_resolve and game_date is None,
    )
    cfb = get_cfb_scores_today(
        game_date=game_date,
        auto_resolve=auto_resolve and game_date is None,
        force_live=game_date is None,
    )
    ufc = get_ufc_scores_today(
        game_date=game_date,
        auto_resolve=auto_resolve and game_date is None,
        force_live=game_date is None,
    )
    games = (
        _tag_sport(mlb.get("games") or [], "mlb")
        + _tag_sport(nba.get("games") or [], "nba")
        + _tag_sport(cfb.get("games") or [], "cfb")
        + _tag_sport(ufc.get("games") or [], "ufc")
    )
    games.sort(key=lambda g: g.get("start_time_utc") or "")

    return {
        "sport": "all",
        "date": nba.get("date") or cfb.get("date") or mlb_date.isoformat(),
        "requested_date": (game_date or date.today()).isoformat(),
        "resolved_date": nba.get("resolved_date")
        or cfb.get("resolved_date")
        or ufc.get("resolved_date")
        or mlb_date.isoformat(),
        "days_ahead": max(
            nba.get("days_ahead", 0),
            cfb.get("days_ahead", 0),
            ufc.get("days_ahead", 0),
        ),
        "auto_advanced": bool(nba.get("auto_advanced"))
        or bool(cfb.get("auto_advanced"))
        or bool(ufc.get("auto_advanced")),
        "games": games,
        "games_count": len(games),
        "sports": {
            "mlb": mlb.get("games_count", 0),
            "nba": nba.get("games_count", 0),
            "cfb": cfb.get("games_count", 0),
            "ufc": ufc.get("games_count", 0),
        },
        "cached_at": max(
            mlb.get("cached_at") or "",
            nba.get("cached_at") or "",
            cfb.get("cached_at") or "",
            ufc.get("cached_at") or "",
        ),
        "cache_hit": (
            bool(mlb.get("cache_hit"))
            and bool(nba.get("cache_hit"))
            and bool(cfb.get("cache_hit"))
            and bool(ufc.get("cache_hit"))
        ),
        "source": "merged",
    }
