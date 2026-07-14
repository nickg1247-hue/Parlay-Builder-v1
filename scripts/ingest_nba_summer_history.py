"""Ingest NBA Summer League historical games from ESPN scoreboards.

Fetches Las Vegas Summer League (required) and California Classic (optional)
scoreboards for July 5–20 across 2023–2025, then writes parquet + CSV.

Usage (from project root):
    python scripts/ingest_nba_summer_history.py
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.scores_nba_summer import (  # noqa: E402
    ESPN_SUMMER_BASE,
    live_game_record,
)

YEARS = (2023, 2024, 2025)
START_MD = (7, 5)
END_MD = (7, 20)

PRIMARY_LEAGUE = "nba-summer-las-vegas"
OPTIONAL_LEAGUES = ("nba-summer-california",)

OUT_PARQUET = ROOT / "data" / "processed" / "nba_summer_games.parquet"
OUT_CSV = ROOT / "data" / "processed" / "nba_summer_games.csv"

REQUEST_SLEEP = 0.15


def _daterange(year: int) -> list[date]:
    start = date(year, *START_MD)
    end = date(year, *END_MD)
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _fetch_scoreboard(
    client: httpx.Client, league: str, game_date: date
) -> list[dict[str, Any]]:
    url = f"{ESPN_SUMMER_BASE}/{league}/scoreboard"
    params = {"dates": game_date.strftime("%Y%m%d")}
    response = client.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    events = list(data.get("events") or [])
    for event in events:
        event["_summer_league"] = league
    return events


def _game_calendar_date(event: dict[str, Any], fallback: date) -> str:
    raw = event.get("date") or ""
    if not raw:
        return fallback.isoformat()
    try:
        # ESPN returns ISO UTC; summer slate is evening PT — keep calendar day from
        # the request date when the UTC stamp rolls past midnight.
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # Prefer the scoreboard query date for stability across TZ.
        return fallback.isoformat()
    except ValueError:
        return str(raw)[:10] or fallback.isoformat()


def _history_row(event: dict[str, Any], *, season_year: int, game_date: date) -> dict[str, Any]:
    rec = live_game_record(event)
    home_score = rec.get("home_score")
    away_score = rec.get("away_score")
    status = rec.get("status") or ""
    home_win: int | None = None
    if (
        status == "Final"
        and home_score is not None
        and away_score is not None
    ):
        home_win = 1 if int(home_score) > int(away_score) else 0
    return {
        "game_id": str(rec.get("game_id") or event.get("id") or ""),
        "date": _game_calendar_date(event, game_date),
        "season_year": season_year,
        "home_team": rec.get("home_team") or "Home",
        "away_team": rec.get("away_team") or "Away",
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
        "status": status,
    }


def ingest() -> pd.DataFrame:
    by_id: dict[str, dict[str, Any]] = {}
    fetch_ok = 0
    fetch_fail = 0
    optional_fail = 0

    with httpx.Client(timeout=30.0) as client:
        for year in YEARS:
            for day in _daterange(year):
                # Primary league
                try:
                    events = _fetch_scoreboard(client, PRIMARY_LEAGUE, day)
                    fetch_ok += 1
                except Exception as exc:
                    fetch_fail += 1
                    print(f"WARN primary {PRIMARY_LEAGUE} {day}: {exc}")
                    events = []

                # Optional California Classic (and skip quietly on failure)
                for league in OPTIONAL_LEAGUES:
                    try:
                        extra = _fetch_scoreboard(client, league, day)
                        events.extend(extra)
                        fetch_ok += 1
                    except Exception as exc:
                        optional_fail += 1
                        print(f"SKIP optional {league} {day}: {exc}")

                for event in events:
                    row = _history_row(event, season_year=year, game_date=day)
                    gid = row["game_id"]
                    if not gid:
                        continue
                    # Prefer Final / scored rows when deduping
                    prev = by_id.get(gid)
                    if prev is None:
                        by_id[gid] = row
                    elif (prev.get("status") != "Final") and (row.get("status") == "Final"):
                        by_id[gid] = row
                    elif prev.get("home_score") is None and row.get("home_score") is not None:
                        by_id[gid] = row

                if REQUEST_SLEEP:
                    import time

                    time.sleep(REQUEST_SLEEP)

    rows = list(by_id.values())
    df = pd.DataFrame(rows)
    if df.empty:
        print("No games found.")
        return df

    df = df.sort_values(["season_year", "date", "game_id"]).reset_index(drop=True)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    df.to_csv(OUT_CSV, index=False)

    print(f"Wrote {len(df)} unique games -> {OUT_PARQUET}")
    print(f"Wrote CSV -> {OUT_CSV}")
    print(f"Fetches ok={fetch_ok} primary_fail={fetch_fail} optional_fail={optional_fail}")

    counts = Counter(df["season_year"].tolist())
    print("\nCounts by season_year:")
    for year in YEARS:
        print(f"  {year}: {counts.get(year, 0)}")
    print(f"  total: {len(df)}")

    finals = df[df["home_win"].notna()]
    if len(finals):
        home_wr = float(finals["home_win"].mean())
        print(
            f"\nHome win rate (Final with scores): "
            f"{home_wr:.3f} ({int(finals['home_win'].sum())}/{len(finals)})"
        )
    else:
        print("\nNo Final games with scores for home win rate.")

    print("\nSample rows:")
    print(df.head(8).to_string(index=False))
    return df


if __name__ == "__main__":
    ingest()
