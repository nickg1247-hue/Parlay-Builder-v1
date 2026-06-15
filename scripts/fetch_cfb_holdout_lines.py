"""Bulk-fetch CFBD betting lines for holdout season dates into cfb_lines_cache/."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.cfb_baseline import HOLDOUT_SEASON, load_games
from app.odds.cfb_betting_lines import (
    LINES_CACHE_DIR,
    _api_key,
    _write_lines_cache,
    cfb_season_end_year,
    fetch_cfbd_lines_for_week,
    get_cfbd_book_games_for_date,
    load_cfbd_calendar,
    resolve_season_week,
)


def _holdout_dates(season: int) -> list[date]:
    games = load_games()
    holdout = games[games["season"] == season]
    if holdout.empty:
        raise SystemExit(f"No games for season {season} in cfb_games.parquet")
    return sorted(
        {pd.to_datetime(d).date() for d in holdout["date"]},
    )


def _group_dates_by_week(dates: list[date]) -> tuple[dict[tuple[int, str, int], list[date]], list[date]]:
    calendars: dict[int, list] = {}
    by_week: dict[tuple[int, str, int], list[date]] = defaultdict(list)
    unresolved: list[date] = []

    for game_date in dates:
        season = cfb_season_end_year(game_date)
        if season not in calendars:
            calendars[season] = load_cfbd_calendar(season)
        resolved = resolve_season_week(game_date, calendars[season]) if calendars[season] else None
        if resolved is None:
            unresolved.append(game_date)
            continue
        season_type, week = resolved
        by_week[(season, season_type, week)].append(game_date)

    return dict(by_week), unresolved


def _write_week_to_date_caches(
    week_games: list[dict],
    dates: list[date],
    *,
    season: int,
    season_type: str,
    week: int,
) -> int:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for game in week_games:
        gd = str(game.get("game_date") or game.get("date") or "")[:10]
        if gd:
            by_date[gd].append(game)

    cached_games = 0
    for game_date in dates:
        iso = game_date.isoformat()
        day_games = by_date.get(iso, [])
        if not day_games:
            continue
        _write_lines_cache(
            game_date,
            day_games,
            source="cfbd",
            meta={"season": season, "season_type": season_type, "week": week},
        )
        cached_games += len(day_games)
    return cached_games


def fetch_holdout_lines(
    season: int = HOLDOUT_SEASON,
    *,
    force: bool = False,
) -> dict[str, int]:
    api_key = _api_key()
    if not api_key:
        raise SystemExit(
            "CFBD_API_KEY is not set. Add your key to .env (see .env.example)."
        )

    dates = _holdout_dates(season)
    by_week, unresolved = _group_dates_by_week(dates)

    dates_written = 0
    games_cached = 0
    api_calls = 0
    errors = 0

    print(f"Holdout season {season}: {len(dates)} unique dates, {len(by_week)} week fetches")

    for (sy, season_type, week), week_dates in sorted(by_week.items()):
        if not force and all(
            (LINES_CACHE_DIR / f"{d.isoformat()}.json").exists() for d in week_dates
        ):
            print(f"  Skip {sy} {season_type} w{week} (all {len(week_dates)} dates cached)")
            continue
        print(f"  Fetch {sy} {season_type} week {week} ({len(week_dates)} holdout dates)...")
        try:
            week_games = fetch_cfbd_lines_for_week(
                sy, week, season_type=season_type, api_key=api_key
            )
            api_calls += 1
        except Exception as exc:
            print(f"    ERROR: {exc}")
            errors += 1
            continue
        if not week_games:
            print("    WARNING: empty week response")
            errors += 1
            continue
        n = _write_week_to_date_caches(
            week_games,
            week_dates,
            season=sy,
            season_type=season_type,
            week=week,
        )
        written = sum(
            1 for d in week_dates if (LINES_CACHE_DIR / f"{d.isoformat()}.json").exists()
        )
        dates_written += written
        games_cached += n
        print(f"    Cached {n} games across {written} date files")

    for game_date in unresolved:
        iso = game_date.isoformat()
        cache_path = LINES_CACHE_DIR / f"{iso}.json"
        if not force and cache_path.exists():
            print(f"  Skip unresolved date {iso} (cached)")
            continue
        print(f"  Fallback fetch for {iso} (calendar miss)...")
        try:
            day_games = get_cfbd_book_games_for_date(game_date, force_refresh=True)
            api_calls += 1
        except Exception as exc:
            print(f"    ERROR: {exc}")
            errors += 1
            continue
        if day_games:
            games_cached += len(day_games)
            dates_written += 1
            print(f"    Cached {len(day_games)} games")
        else:
            errors += 1
            print("    WARNING: no lines returned")

    return {
        "holdout_dates": len(dates),
        "dates_cached": dates_written,
        "games_cached": games_cached,
        "api_calls": api_calls,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-fetch CFBD lines for holdout dates into cfb_lines_cache/"
    )
    parser.add_argument(
        "--season",
        type=int,
        default=HOLDOUT_SEASON,
        help=f"Holdout season end-year (default {HOLDOUT_SEASON})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even when date cache files exist",
    )
    args = parser.parse_args()

    stats = fetch_holdout_lines(args.season, force=args.force)

    print("\n--- Summary ---")
    print(f"Unique holdout dates: {stats['holdout_dates']}")
    print(f"Date cache files written/updated: {stats['dates_cached']}")
    print(f"Games cached: {stats['games_cached']}")
    print(f"CFBD API calls: {stats['api_calls']}")
    print(f"Errors/warnings: {stats['errors']}")
    print(f"Cache dir: {LINES_CACHE_DIR}")

    if stats["games_cached"] == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
