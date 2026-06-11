"""CFB slate games from ingested history (cfb_games.parquet) — no ESPN API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.models.cfb_baseline import load_games
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_team_logos import enrich_game_logos


def _date_str(game_date: date) -> str:
    return game_date.isoformat()


def games_from_ingest(game_date: date) -> list[dict[str, Any]]:
    """FBS games on *game_date* from saved ingest; empty if parquet missing."""
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
        home_score = int(row.home_score)
        away_score = int(row.away_score)
        kickoff = datetime.combine(
            game_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        out.append(
            enrich_game_logos(
                {
                    "sport": "cfb",
                    "game_id": str(row.game_id),
                    "home_team": normalize_team_name(str(row.home_team)),
                    "away_team": normalize_team_name(str(row.away_team)),
                    "home_team_id": None,
                    "away_team_id": None,
                    "home_team_abbr": None,
                    "away_team_abbr": None,
                    "home_logo_url": None,
                    "away_logo_url": None,
                    "home_record": None,
                    "away_record": None,
                    "start_time_utc": kickoff,
                    "status": "Final",
                    "detailed_status": "Final",
                    "period_label": None,
                    "home_score": home_score,
                    "away_score": away_score,
                    "season": int(row.season),
                    "from_ingest": True,
                }
            )
        )
    return out


def ingest_has_games(game_date: date) -> bool:
    return bool(games_from_ingest(game_date))
