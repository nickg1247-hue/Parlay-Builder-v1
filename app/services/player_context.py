"""Player prop context — recent game logs vs a line (MLB v1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.prop_scoring import (
    MARKET_STAT,
    _hit_rates,
    _http_client_get,
    _search_player_id,
    _stat_value,
    market_label,
)
from app.services.teams_hub import _mlb_player_photo

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _season_game_log_rows(
    player_id: int, group: str, stat_key: str, season: int
) -> list[dict[str, Any]]:
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "gameLog", "group": group, "season": season}
    try:
        response = _http_client_get().get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    stats_blocks = payload.get("stats") or []
    if not stats_blocks:
        return []
    rows: list[dict[str, Any]] = []
    for split in stats_blocks[0].get("splits") or []:
        stat = split.get("stat") or {}
        val = _stat_value(group, stat_key, stat)
        if val is None:
            continue
        game = split.get("game") or {}
        opp = split.get("opponent") or {}
        raw_date = split.get("date") or game.get("gameDate") or ""
        rows.append(
            {
                "date": str(raw_date)[:10],
                "opponent": opp.get("name") or opp.get("abbreviation") or "—",
                "is_home": split.get("isHome"),
                "stat_value": val,
            }
        )
    return rows


def get_player_prop_context(
    sport: str,
    player_id: str,
    *,
    market_type: str,
    line: float,
    side: str,
    season: int | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """Recent games vs prop line for modal display."""
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

    log_rows = _season_game_log_rows(pid, group, stat_key, yr)
    values = [r["stat_value"] for r in log_rows]

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

    recent: list[dict[str, Any]] = []
    for row in log_rows[:limit]:
        val = row["stat_value"]
        if hit_side == "over":
            hit = val > line
            push = val == line
        else:
            hit = val < line
            push = val == line
        recent.append(
            {
                **row,
                "hit": None if push else hit,
                "vs_line": f"{'O' if val > line else 'U' if val < line else 'P'} {line:g}",
            }
        )

    l5 = rate_for(l5_vals)
    l10 = rate_for(l10_vals)
    season_r = rate_for(values)

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
        "hit_rates": {
            "l5": l5["side"],
            "l10": l10["side"],
            "season": season_r["side"],
        },
        "sample_games": len(values),
        "recent_games": recent,
    }


def resolve_player_id_for_name(sport: str, player_name: str) -> int | None:
    if sport != "mlb":
        return None
    return _search_player_id(player_name)
