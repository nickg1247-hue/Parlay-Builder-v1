"""UFC card fights from ingested history (ufc_fights.parquet)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.models.ufc_baseline import load_fights
from app.odds.ufc_fighter_aliases import normalize_fighter_name


def fights_from_ingest(game_date: date) -> list[dict[str, Any]]:
    try:
        fights = load_fights()
    except FileNotFoundError:
        return []

    day = game_date.isoformat()
    dates = pd.to_datetime(fights["date"]).dt.strftime("%Y-%m-%d")
    rows = fights[dates == day].sort_values(["fight_id"])
    if rows.empty:
        return []

    out: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        kickoff = datetime.combine(
            game_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        home_win = int(row.home_win)
        out.append(
            {
                "sport": "ufc",
                "game_id": str(row.fight_id),
                "fight_id": str(row.fight_id),
                "event_id": str(row.event_id),
                "event_name": str(row.event_name),
                "home_team": normalize_fighter_name(str(row.home_team)),
                "away_team": normalize_fighter_name(str(row.away_team)),
                "home_fighter": normalize_fighter_name(str(row.home_team)),
                "away_fighter": normalize_fighter_name(str(row.away_team)),
                "weight_class": str(getattr(row, "weight_class", "") or ""),
                "card_segment": str(getattr(row, "card_segment", "") or ""),
                "start_time_utc": kickoff,
                "status": "Final",
                "detailed_status": "Final",
                "home_winner": bool(home_win),
                "away_winner": not bool(home_win),
                "winner": normalize_fighter_name(str(row.home_team))
                if home_win
                else normalize_fighter_name(str(row.away_team)),
                "from_ingest": True,
            }
        )
    return out


def ingest_has_fights(game_date: date) -> bool:
    return bool(fights_from_ingest(game_date))
