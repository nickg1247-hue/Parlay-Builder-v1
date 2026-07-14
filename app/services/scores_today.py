"""Unified today's scores for MLB, NBA, CFB, UFC, NBA Summer, and multi-sport ticker."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from app.services.scores_cfb import get_cfb_scores_today
from app.services.scores_mlb import get_scores_today as get_mlb_scores_today
from app.services.scores_nba import get_nba_scores_today
from app.services.scores_nba_summer import get_nba_summer_scores_today, summer_enabled
from app.services.scores_ufc import get_ufc_scores_today

SUPPORTED_SPORTS = ("mlb", "nba", "nba-summer", "cfb", "ufc", "all")


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

    if sport == "nba-summer":
        return get_nba_summer_scores_today(
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

    def _fetch_mlb() -> dict[str, Any]:
        return get_mlb_scores_today(game_date=mlb_date)

    def _fetch_nba() -> dict[str, Any]:
        return get_nba_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
        )

    def _fetch_nba_summer() -> dict[str, Any]:
        if not summer_enabled():
            return {"games": [], "games_count": 0, "cache_hit": True}
        return get_nba_summer_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
        )

    def _fetch_cfb() -> dict[str, Any]:
        return get_cfb_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
            force_live=game_date is None,
        )

    def _fetch_ufc() -> dict[str, Any]:
        return get_ufc_scores_today(
            game_date=game_date,
            auto_resolve=auto_resolve and game_date is None,
            force_live=game_date is None,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        mlb_future = pool.submit(_fetch_mlb)
        nba_future = pool.submit(_fetch_nba)
        summer_future = pool.submit(_fetch_nba_summer)
        cfb_future = pool.submit(_fetch_cfb)
        ufc_future = pool.submit(_fetch_ufc)
        mlb = mlb_future.result()
        nba = nba_future.result()
        summer = summer_future.result()
        cfb = cfb_future.result()
        ufc = ufc_future.result()
    games = (
        _tag_sport(mlb.get("games") or [], "mlb")
        + _tag_sport(nba.get("games") or [], "nba")
        + _tag_sport(summer.get("games") or [], "nba-summer")
        + _tag_sport(cfb.get("games") or [], "cfb")
        + _tag_sport(ufc.get("games") or [], "ufc")
    )
    games.sort(key=lambda g: g.get("start_time_utc") or "")

    return {
        "sport": "all",
        "date": nba.get("date")
        or summer.get("date")
        or cfb.get("date")
        or mlb_date.isoformat(),
        "requested_date": (game_date or date.today()).isoformat(),
        "resolved_date": nba.get("resolved_date")
        or summer.get("resolved_date")
        or cfb.get("resolved_date")
        or ufc.get("resolved_date")
        or mlb_date.isoformat(),
        "days_ahead": max(
            nba.get("days_ahead", 0),
            summer.get("days_ahead", 0),
            cfb.get("days_ahead", 0),
            ufc.get("days_ahead", 0),
        ),
        "auto_advanced": bool(nba.get("auto_advanced"))
        or bool(summer.get("auto_advanced"))
        or bool(cfb.get("auto_advanced"))
        or bool(ufc.get("auto_advanced")),
        "games": games,
        "games_count": len(games),
        "sports": {
            "mlb": mlb.get("games_count", 0),
            "nba": nba.get("games_count", 0),
            "nba-summer": summer.get("games_count", 0),
            "cfb": cfb.get("games_count", 0),
            "ufc": ufc.get("games_count", 0),
        },
        "cached_at": max(
            mlb.get("cached_at") or "",
            nba.get("cached_at") or "",
            summer.get("cached_at") or "",
            cfb.get("cached_at") or "",
            ufc.get("cached_at") or "",
        ),
        "cache_hit": (
            bool(mlb.get("cache_hit"))
            and bool(nba.get("cache_hit"))
            and bool(summer.get("cache_hit"))
            and bool(cfb.get("cache_hit"))
            and bool(ufc.get("cache_hit"))
        ),
        "source": "merged",
    }
