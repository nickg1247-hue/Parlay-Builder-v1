"""Match ESPN slate rows to CFBD / Odds API games by date + normalized team names."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.odds.cfb_team_aliases import normalize_team_name


def _date_iso(game_date: date | str) -> str:
    if isinstance(game_date, date):
        return game_date.isoformat()
    return str(game_date)[:10]


def match_key(
    game_date: date | str,
    home_team: str,
    away_team: str,
) -> tuple[str, str, str]:
    """Canonical lookup key: (YYYY-MM-DD, home, away) with alias normalization."""
    return (
        _date_iso(game_date),
        normalize_team_name(home_team),
        normalize_team_name(away_team),
    )


def build_cfbd_lines_index(
    cfbd_games: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index CFBD line rows by match_key."""
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in cfbd_games:
        home = normalize_team_name(str(row.get("home_team") or row.get("homeTeam") or ""))
        away = normalize_team_name(str(row.get("away_team") or row.get("awayTeam") or ""))
        gd = row.get("game_date") or row.get("date") or ""
        if not home or not away or not gd:
            continue
        key = match_key(str(gd)[:10], home, away)
        index[key] = row
    return index


def resolve_cfbd_game_id(
    slate_row: pd.Series | dict[str, Any],
    cfbd_lines_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> str | None:
    """Resolve CFBD game id from slate row via team + date match."""
    if isinstance(slate_row, pd.Series):
        home = slate_row.get("home_team", "")
        away = slate_row.get("away_team", "")
        gd = slate_row.get("date", "")
    else:
        home = slate_row.get("home_team", "")
        away = slate_row.get("away_team", "")
        gd = slate_row.get("date", "")
    key = match_key(str(gd)[:10], str(home), str(away))
    hit = cfbd_lines_by_key.get(key)
    if not hit:
        return None
    gid = hit.get("cfbd_game_id") or hit.get("game_id") or hit.get("id")
    return str(gid) if gid is not None else None


def attach_cfbd_ids_to_slate(
    slate_df: pd.DataFrame,
    cfbd_lines_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> pd.DataFrame:
    """Add cfbd_game_id column when a CFBD match exists."""
    out = slate_df.copy()
    cfbd_ids: list[str | None] = []
    for row in out.itertuples(index=False):
        series = pd.Series(
            {
                "home_team": row.home_team,
                "away_team": row.away_team,
                "date": row.date,
            }
        )
        cfbd_ids.append(resolve_cfbd_game_id(series, cfbd_lines_by_key))
    out["cfbd_game_id"] = cfbd_ids
    return out
