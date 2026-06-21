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
    _stat_value,
    market_label,
)
from app.services.mlb_player_depth import get_mlb_player_depth
from app.services.teams_hub import _mlb_player_photo

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _season_stat_values(
    player_id: int, group: str, stat_key: str, season: int
) -> list[float]:
    log = fetch_mlb_season_game_log(player_id, group=group, season=season, limit=80)
    col_key = stat_key if stat_key != "_outs" else "outs"
    if stat_key == "_outs":
        col_key = "outs"
    values: list[float] = []
    for g in log.get("games") or []:
        raw = g.get("stats", {}).get(col_key)
        if raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return values


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
    if sport != "mlb":
        return {
            "sport": sport,
            "status": "unsupported",
            "message": "Prop context available for MLB only in v1.",
        }

    mapping = MARKET_STAT.get(market_type)
    if not mapping:
        return {"status": "error", "message": f"Unknown market: {market_type}"}

    group, stat_key = mapping
    pid = int(player_id)
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

    l5_vals = values[:5]
    l10_vals = values[:10]
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


def resolve_player_id_for_name(sport: str, player_name: str) -> int | None:
    if sport != "mlb":
        return None
    return _search_player_id(player_name)
