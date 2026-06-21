"""Player profile for team-page modals (MLB v1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.player_context import _season_game_log_rows
from app.services.prop_scoring import _http_client_get, market_label
from app.services.props_mlb import build_daily_top_props
from app.services.teams_hub import _mlb_player_photo

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _person_payload(player_id: int) -> dict[str, Any]:
    try:
        resp = _http_client_get().get(f"{MLB_STATS_BASE}/people/{player_id}")
        resp.raise_for_status()
        people = resp.json().get("people") or []
        return people[0] if people else {}
    except Exception:
        return {}


def _season_stat_block(player_id: int, group: str, season: int) -> dict[str, Any]:
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "season", "group": group, "season": season}
    try:
        resp = _http_client_get().get(url, params=params)
        resp.raise_for_status()
        blocks = resp.json().get("stats") or []
        if not blocks:
            return {}
        splits = blocks[0].get("splits") or []
        return splits[0].get("stat") if splits else {}
    except Exception:
        return {}


def _recent_batting_games(player_id: int, season: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = _season_game_log_rows(player_id, "hitting", "hits", season)
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        # Fetch full batting line for that game via gameLog hitting split
        out.append(
            {
                "date": row["date"],
                "opponent": row["opponent"],
                "line": row.get("stat_value"),
            }
        )
    return out


def _props_for_player(player_name: str, game_date: date | None = None) -> list[dict[str, Any]]:
    gd = game_date or date.today()
    slate = build_daily_top_props(gd, limit=30, scan=False)
    all_props = (slate.get("top_props") or []) + (slate.get("very_strong_props") or [])
    key = player_name.strip().lower()
    return [p for p in all_props if str(p.get("player", "")).strip().lower() == key]


def get_player_profile(sport: str, player_id: str) -> dict[str, Any]:
    if sport != "mlb":
        return {
            "sport": sport,
            "status": "unsupported",
            "message": "Full player profiles available for MLB in v1.",
            "available_props": [],
        }

    pid = int(player_id)
    person = _person_payload(pid)
    if not person:
        return {"status": "error", "message": "Player not found"}

    name = person.get("fullName") or ""
    season = date.today().year
    primary = person.get("primaryPosition") or {}
    pos_code = primary.get("abbreviation") or primary.get("code") or ""
    is_pitcher = pos_code in ("P", "SP", "RP") or str(primary.get("type") or "").lower() == "pitcher"

    group = "pitching" if is_pitcher else "hitting"
    season_stats = _season_stat_block(pid, group, season)
    career_stats = _season_stat_block(pid, group, 0)  # MLB API career via season=0 sometimes fails

    # Career via stats=career
    try:
        resp = _http_client_get().get(
            f"{MLB_STATS_BASE}/people/{pid}/stats",
            params={"stats": "career", "group": group},
        )
        blocks = resp.json().get("stats") or []
        if blocks and blocks[0].get("splits"):
            career_stats = blocks[0]["splits"][0].get("stat") or career_stats
    except Exception:
        pass

    if is_pitcher:
        recent = _season_game_log_rows(pid, "pitching", "strikeOuts", season)[:12]
        for r in recent:
            r["summary"] = f"{r['stat_value']:.0f} K"
    else:
        hit_rows = _season_game_log_rows(pid, "hitting", "hits", season)[:12]
        recent = []
        for r in hit_rows:
            recent.append(
                {
                    "date": r["date"],
                    "opponent": r["opponent"],
                    "summary": f"{r['stat_value']:.0f} H",
                }
            )

    props = _props_for_player(name)

    return {
        "status": "ok",
        "sport": "mlb",
        "player_id": str(player_id),
        "name": name,
        "position": pos_code,
        "photo_url": _mlb_player_photo(pid),
        "career_stats": career_stats,
        "season_stats": season_stats,
        "season": season,
        "recent_games": recent,
        "available_props": [
            {
                "player": p.get("player"),
                "market_type": p.get("market_type"),
                "market_label": p.get("market_label") or market_label(str(p.get("market_type", ""))),
                "line": p.get("line"),
                "recommended_side": p.get("recommended_side"),
                "recommended_odds": p.get("recommended_odds"),
                "recommended_hit_rate": p.get("recommended_hit_rate"),
                "rank_score": p.get("rank_score"),
                "line_insight": p.get("line_insight"),
                "game_id": p.get("game_id"),
                "matchup": p.get("matchup"),
            }
            for p in props
        ],
    }
