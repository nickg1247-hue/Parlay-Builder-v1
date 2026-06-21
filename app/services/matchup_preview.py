"""Compact matchup preview payload for dedicated preview pages."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.game_insights import build_game_insights
from app.services.mlb_game_lineup import get_mlb_game_lineup


def build_matchup_preview(
    sport: str,
    game_id: str,
    *,
    game_date: date | None = None,
    use_cache: bool = False,
) -> dict[str, Any]:
    if sport != "mlb":
        return {
            "status": "unsupported",
            "message": "Matchup previews available for MLB in v1.",
        }

    gd = game_date or date.today()
    insights = build_game_insights(game_id, gd, use_cache=use_cache)
    if not insights:
        return {"status": "error", "message": "Game not found"}

    lineup = get_mlb_game_lineup(game_id, gd)
    game = insights.get("game") or {}
    venue = game.get("venue") or {}
    officials = game.get("officials") or []

    return {
        "status": "ok",
        "sport": "mlb",
        "game_id": str(game_id),
        "date": gd.isoformat(),
        "game": game,
        "venue": {
            "name": venue.get("name") or game.get("venue_name"),
            "city": venue.get("city"),
        },
        "umpires": [
            {"name": o.get("official", {}).get("fullName"), "position": o.get("officialType")}
            for o in officials
            if isinstance(o, dict)
        ],
        "market_cards": insights.get("market_cards"),
        "model": insights.get("model"),
        "highlights": insights.get("highlights"),
        "explanation": insights.get("explanation"),
        "recent_games": insights.get("recent_games"),
        "lineup": lineup,
        "warnings": insights.get("warnings") or [],
        "preview_url": f"/preview/mlb/{game_id}",
        "game_url": f"/mlb/game/{game_id}",
    }
