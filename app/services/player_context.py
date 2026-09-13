"""Player prop context — recent game logs vs a line (MLB v1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.mlb_game_log import (
    MARKET_STAT_COLUMN,
    annotate_prop_line_hits,
    fetch_mlb_season_game_log,
)
from app.services.prop_scoring import (
    MARKET_STAT,
    _hit_rates,
    _http_client_get,
    _search_player_id,
    _season_game_log_values,
    market_label,
    recent_game_window,
)
from app.services.mlb_player_depth import get_mlb_player_depth
from app.services.teams_hub import _mlb_player_photo

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _season_stat_values(
    player_id: int, group: str, stat_key: str, season: int
) -> list[float]:
    return list(_season_game_log_values(player_id, group, stat_key, season))


def get_player_prop_context(
    sport: str,
    player_id: str,
    *,
    market_type: str,
    line: float,
    side: str,
    season: int | None = None,
    limit: int = 20,
    game_id: str | None = None,
) -> dict[str, Any]:
    """Recent games vs prop line + full season stat table."""
    if sport == "nfl":
        return get_nfl_player_prop_context(
            player_id,
            market_type=market_type,
            line=line,
            side=side,
            season=season,
        )
    if sport != "mlb":
        return {
            "sport": sport,
            "status": "unsupported",
            "message": "Prop context available for MLB and NFL.",
        }

    mapping = MARKET_STAT.get(market_type)
    if not mapping:
        return {"status": "error", "message": f"Unknown market: {market_type}"}

    group, stat_key = mapping
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid player id"}
    yr = season or date.today().year
    person_url = f"{MLB_STATS_BASE}/people/{pid}"
    player_name = ""
    try:
        person = _http_client_get().get(person_url).json().get("people") or []
        if person:
            player_name = person[0].get("fullName") or ""
    except Exception:
        pass

    game_log = fetch_mlb_season_game_log(pid, group=group, season=yr, limit=limit)
    games = annotate_prop_line_hits(
        game_log.get("games") or [],
        market_type=market_type,
        line=line,
        side=side,
    )
    values = _season_stat_values(pid, group, stat_key, yr)

    def rate_for(vals: list[float]) -> dict[str, float | None]:
        over, under = _hit_rates(vals, line)
        return {
            "over": over,
            "under": under,
            "side": under if side == "under" else over,
        }

    l5_vals = recent_game_window(values, 5)
    l10_vals = recent_game_window(values, 10)
    hit_side = side if side in ("over", "under") else "over"
    prop_col = MARKET_STAT_COLUMN.get(market_type)

    recent: list[dict[str, Any]] = []
    for row in games:
        stat_val = row.get("stats", {}).get(prop_col) if prop_col else None
        recent.append(
            {
                "date": row.get("date"),
                "opponent": row.get("opponent"),
                "stat_value": stat_val,
                "hit": row.get("prop_hit"),
                "stats": row.get("stats"),
            }
        )

    l5 = rate_for(l5_vals)
    l10 = rate_for(l10_vals)
    season_r = rate_for(values)

    depth = get_mlb_player_depth(
        pid,
        game_id=game_id,
        market_type=market_type,
        season=yr,
    )

    return {
        "status": "ok",
        "sport": "mlb",
        "player_id": str(player_id),
        "player_name": player_name,
        "photo_url": _mlb_player_photo(pid),
        "market_type": market_type,
        "market_label": market_label(market_type),
        "line": line,
        "side": hit_side,
        "season": yr,
        "prop_stat_key": prop_col,
        "hit_rates": {
            "l5": l5["side"],
            "l10": l10["side"],
            "season": season_r["side"],
        },
        "sample_games": len(values),
        "recent_games": recent,
        "game_log": {
            **game_log,
            "games": games,
            "highlight_column": prop_col,
        },
        "depth": depth,
    }


def get_nfl_player_prop_context(
    player_id: str,
    *,
    market_type: str,
    line: float,
    side: str,
    season: int | None = None,
) -> dict[str, Any]:
    from app.services.nfl_player_stats import nfl_game_log_entries
    from app.services.prop_engine.nfl_context import hit_rates_vs_line, recommended_hit_rates
    from app.services.prop_engine.nfl_markets import MARKET_STAT, market_label as nfl_market_label

    stat_key = MARKET_STAT.get(market_type)
    if not stat_key:
        return {"status": "error", "message": f"Unknown market: {market_type}"}
    entries = nfl_game_log_entries(str(player_id), stat_key)
    if season:
        entries = [row for row in entries if str(row.get("date") or "").startswith(str(season))]
    values = [float(row["stat_value"]) for row in entries]
    hit_side = side if side in ("over", "under") else "over"
    rates = hit_rates_vs_line(values, float(line))
    rec = recommended_hit_rates(rates, hit_side)
    games = []
    for row in reversed(entries[-20:]):
        val = float(row["stat_value"])
        hit = val > float(line) if hit_side == "over" else val < float(line)
        games.append(
            {
                "date": row.get("date"),
                "opponent": row.get("opponent") or "",
                "prop_hit": hit,
                "stats": {"stat": val, **(row.get("stats") or {})},
            }
        )
    return {
        "status": "ok",
        "sport": "nfl",
        "player_id": str(player_id),
        "player_name": "",
        "photo_url": None,
        "market_type": market_type,
        "market_label": nfl_market_label(market_type),
        "line": float(line),
        "side": hit_side,
        "season": season or date.today().year,
        "prop_stat_key": "stat",
        "hit_rates": rec,
        "sample_games": len(values),
        "recent_games": games,
        "game_log": {
            "columns": [{"key": "stat", "label": nfl_market_label(market_type)}],
            "games": games,
            "highlight_column": "stat",
        },
        "depth": {},
    }


def resolve_player_id_for_name(sport: str, player_name: str) -> int | None:
    if sport != "mlb":
        return None
    return _search_player_id(player_name)
