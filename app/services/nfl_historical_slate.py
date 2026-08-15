"""NFL slate games from ingested history (nfl_games.parquet) — no ESPN API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.models.nfl_baseline import load_games

_INGEST_DATES: set[str] | None = None


def _date_str(game_date: date) -> str:
    return game_date.isoformat()


def ingest_dates() -> set[str]:
    global _INGEST_DATES
    if _INGEST_DATES is not None:
        return _INGEST_DATES
    try:
        games = load_games()
    except FileNotFoundError:
        _INGEST_DATES = set()
        return _INGEST_DATES
    _INGEST_DATES = set(pd.to_datetime(games["date"]).dt.strftime("%Y-%m-%d"))
    return _INGEST_DATES


def games_from_ingest(game_date: date) -> list[dict[str, Any]]:
    if _date_str(game_date) not in ingest_dates():
        return []
    try:
        games = load_games()
    except FileNotFoundError:
        return []

    day = _date_str(game_date)
    dates = pd.to_datetime(games["date"]).dt.strftime("%Y-%m-%d")
    mask = dates == day
    rows = games[mask].sort_values(["game_id"])
    if rows.empty:
        return []

    out: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        kickoff = (
            datetime.combine(game_date, datetime.min.time(), tzinfo=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        out.append(
            {
                "sport": "nfl",
                "game_id": str(row.game_id),
                "home_team": str(row.home_team),
                "away_team": str(row.away_team),
                "home_team_id": str(getattr(row, "home_team_id", "") or ""),
                "away_team_id": str(getattr(row, "away_team_id", "") or ""),
                "home_team_abbr": str(getattr(row, "home_team_abbr", "") or ""),
                "away_team_abbr": str(getattr(row, "away_team_abbr", "") or ""),
                "home_logo_url": None,
                "away_logo_url": None,
                "home_record": None,
                "away_record": None,
                "start_time_utc": kickoff,
                "status": "Final",
                "detailed_status": "Final",
                "period_label": None,
                "home_score": int(row.home_score),
                "away_score": int(row.away_score),
                "season": int(row.season),
                "week": int(getattr(row, "week", 0) or 0),
                "neutral_site": int(getattr(row, "neutral_site", 0) or 0),
                "game_type": str(getattr(row, "game_type", "regular") or "regular"),
                "is_preseason": int(
                    str(getattr(row, "game_type", "") or "").lower() == "preseason"
                ),
                "espn_home_ml": getattr(row, "espn_home_ml", None),
                "espn_away_ml": getattr(row, "espn_away_ml", None),
                "espn_spread": getattr(row, "espn_spread", None),
                "espn_ou": getattr(row, "espn_ou", None),
                "from_ingest": True,
            }
        )
    return out


def ingest_has_games(game_date: date) -> bool:
    return _date_str(game_date) in ingest_dates()
