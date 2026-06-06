"""Seed odds repository from historical CSV files (no API credits)."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.odds.mlb_odds_free import ODDS_2025_CSV, TOTALS_2025_CSV
from app.odds.odds_repository import import_games_from_csv_rows
from app.odds.team_aliases import normalize_team_name


def _load_csv_dates() -> dict[str, list[dict]]:
    if not ODDS_2025_CSV.exists():
        print(f"Missing {ODDS_2025_CSV}")
        return {}

    odds = pd.read_csv(ODDS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    totals_by_key: dict[tuple[str, str, str], dict] = {}
    if TOTALS_2025_CSV.exists():
        totals = pd.read_csv(TOTALS_2025_CSV)
        totals["date"] = pd.to_datetime(totals["date"]).dt.strftime("%Y-%m-%d")
        for row in totals.itertuples(index=False):
            key = (
                row.date,
                normalize_team_name(row.home_team),
                normalize_team_name(row.away_team),
            )
            totals_by_key[key] = {
                "ou_line": float(row.ou_line) if pd.notna(row.ou_line) else None,
                "over_odds": int(row.over_odds) if pd.notna(getattr(row, "over_odds", None)) else None,
                "under_odds": int(row.under_odds) if pd.notna(getattr(row, "under_odds", None)) else None,
            }

    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in odds.itertuples(index=False):
        d = row.date
        home = normalize_team_name(row.home_team)
        away = normalize_team_name(row.away_team)
        key = (d, home, away)
        t = totals_by_key.get(key, {})
        by_date[d].append(
            {
                "home_team": home,
                "away_team": away,
                "commence_time": f"{d}T00:00:00Z",
                "home_ml": int(row.home_ml) if pd.notna(row.home_ml) else None,
                "away_ml": int(row.away_ml) if pd.notna(row.away_ml) else None,
                "ou_line": t.get("ou_line"),
                "over_odds": t.get("over_odds"),
                "under_odds": t.get("under_odds"),
                "home_spread_point": None,
                "home_spread_american": None,
                "away_spread_point": None,
                "away_spread_american": None,
            }
        )
    return by_date


def main() -> int:
    by_date = _load_csv_dates()
    if not by_date:
        return 1
    for iso, games in sorted(by_date.items()):
        import_games_from_csv_rows(date.fromisoformat(iso), games)
        print(f"Imported {len(games)} games for {iso} (source=csv_import)")
    print(f"Done — {len(by_date)} dates in odds repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
