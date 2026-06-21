"""MLB season game logs — full stat rows for player modals."""

from __future__ import annotations

from typing import Any

from app.services.prop_scoring import (
    _http_client_get,
    _parse_innings_outs,
    _split_game_date_iso,
    _stat_value,
)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"

HITTING_GAME_LOG_COLUMNS: list[dict[str, str]] = [
    {"key": "atBats", "label": "AB"},
    {"key": "runs", "label": "R"},
    {"key": "hits", "label": "H"},
    {"key": "totalBases", "label": "TB"},
    {"key": "homeRuns", "label": "HR"},
    {"key": "rbi", "label": "RBI"},
    {"key": "baseOnBalls", "label": "BB"},
    {"key": "strikeOuts", "label": "SO"},
]

PITCHING_GAME_LOG_COLUMNS: list[dict[str, str]] = [
    {"key": "inningsPitched", "label": "IP"},
    {"key": "hits", "label": "H"},
    {"key": "runs", "label": "R"},
    {"key": "earnedRuns", "label": "ER"},
    {"key": "baseOnBalls", "label": "BB"},
    {"key": "strikeOuts", "label": "K"},
    {"key": "outs", "label": "Outs"},
]

MARKET_STAT_COLUMN: dict[str, str] = {
    "batter_hits": "hits",
    "batter_total_bases": "totalBases",
    "batter_rbis": "rbi",
    "batter_runs_scored": "runs",
    "batter_home_runs": "homeRuns",
    "pitcher_strikeouts": "strikeOuts",
    "pitcher_hits_allowed": "hits",
    "pitcher_earned_runs": "earnedRuns",
    "pitcher_outs": "outs",
}


def _format_cell(group: str, key: str, stat: dict[str, Any]) -> str | int | float | None:
    if key == "outs" and group == "pitching":
        outs = _stat_value("pitching", "_outs", stat)
        return int(outs) if outs is not None else None
    if key == "inningsPitched":
        raw = stat.get("inningsPitched")
        return str(raw) if raw not in (None, "") else None
    val = _stat_value(group, key, stat) if key != "inningsPitched" else None
    if val is None and key in stat:
        try:
            val = float(stat[key])
        except (TypeError, ValueError):
            return None
    if val is None:
        return None
    if float(val).is_integer():
        return int(val)
    return val


def fetch_mlb_season_game_log(
    player_id: int,
    *,
    group: str,
    season: int,
    limit: int = 30,
) -> dict[str, Any]:
    """Return column defs + per-game stat rows (most recent first)."""
    columns = (
        PITCHING_GAME_LOG_COLUMNS if group == "pitching" else HITTING_GAME_LOG_COLUMNS
    )
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "gameLog", "group": group, "season": season}
    try:
        response = _http_client_get().get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {"group": group, "season": season, "columns": columns, "games": []}

    stats_blocks = payload.get("stats") or []
    if not stats_blocks:
        return {"group": group, "season": season, "columns": columns, "games": []}

    games: list[dict[str, Any]] = []
    splits = sorted(
        stats_blocks[0].get("splits") or [],
        key=_split_game_date_iso,
        reverse=True,
    )
    for split in splits:
        stat = split.get("stat") or {}
        opp = split.get("opponent") or {}
        raw_date = split.get("date") or (split.get("game") or {}).get("gameDate") or ""
        home = split.get("isHome")
        prefix = "vs" if home else "@"
        abbr = opp.get("abbreviation") or opp.get("name") or "—"
        row_stats: dict[str, Any] = {}
        for col in columns:
            row_stats[col["key"]] = _format_cell(group, col["key"], stat)
        games.append(
            {
                "date": str(raw_date)[:10],
                "opponent": f"{prefix} {abbr}".strip(),
                "is_home": bool(home),
                "stats": row_stats,
            }
        )

    return {
        "group": group,
        "season": season,
        "columns": columns,
        "games": games[: max(1, min(limit, 80))],
    }


def annotate_prop_line_hits(
    games: list[dict[str, Any]],
    *,
    market_type: str,
    line: float,
    side: str,
) -> list[dict[str, Any]]:
    """Add hit/miss vs prop line on each game row."""
    stat_key = MARKET_STAT_COLUMN.get(market_type)
    if not stat_key:
        return games
    hit_side = side if side in ("over", "under") else "over"
    out: list[dict[str, Any]] = []
    for g in games:
        raw = g.get("stats", {}).get(stat_key)
        hit = None
        if raw is not None:
            try:
                val = float(raw)
                if val == line:
                    hit = None
                elif hit_side == "over":
                    hit = val > line
                else:
                    hit = val < line
            except (TypeError, ValueError):
                pass
        out.append({**g, "prop_hit": hit, "prop_stat_key": stat_key})
    return out
