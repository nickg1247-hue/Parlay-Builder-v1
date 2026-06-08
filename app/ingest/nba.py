"""NBA game ingest: NBA Stats API (stats.nba.com) leaguegamefinder — free, no API key."""

from __future__ import annotations

import calendar
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from nba_api.stats.endpoints import leaguegamefinder

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.nba_schema import NBA_GAMES_COLUMNS, ensure_nba_games_table

logger = logging.getLogger(__name__)

# Canonical names aligned with ESPN displayName (stats.nba.com uses longer variants).
_TEAM_ALIASES: dict[str, str] = {
    "la clippers": "LA Clippers",
    "los angeles clippers": "LA Clippers",
}


def _normalize_team_name(name: str) -> str:
    if not name or not str(name).strip():
        return name
    raw = " ".join(str(name).strip().split())
    return _TEAM_ALIASES.get(raw.lower(), raw)

NBA_STATS_GAME_FINDER = "https://stats.nba.com/stats/leaguegamefinder"
# End-year season integer + stats.nba.com season string.
SEASONS: tuple[tuple[int, str], ...] = (
    (2024, "2023-24"),
    (2025, "2024-25"),
    (2026, "2025-26"),
)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "nba_games.parquet"
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "nba_games.csv"
MAX_REST_GAP_DAYS = 14
DEFAULT_REST_FILL = 2.0
REQUEST_SLEEP_SECONDS = 0.6
REQUEST_RETRIES = 4


@dataclass
class ParsedGame:
    game_id: str
    date: str
    season: int
    season_label: str
    game_type: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


def _is_home_matchup(matchup: str) -> bool:
    return " @ " not in matchup


def _parse_game_date(raw: str) -> str:
    """Use calendar date from NBA Stats GAME_DATE (US/Eastern schedule date, no TZ shift)."""
    return raw[:10]


def _result_set_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, dict):
        sets = [sets]
    if not sets:
        return []
    block = sets[0]
    headers = block.get("headers") or []
    rows: list[dict[str, Any]] = []
    for row in block.get("rowSet") or []:
        rows.append(dict(zip(headers, row, strict=False)))
    return rows


def _parse_team_rows(
    team_rows: list[dict[str, Any]],
    *,
    season: int,
    season_label: str,
    game_type: str,
) -> tuple[list[ParsedGame], int]:
    """Pair home/away team rows by game_id; return completed games and skipped count."""
    by_id: dict[str, dict[str, Any]] = defaultdict(dict)
    skipped = 0

    for row in team_rows:
        game_id = str(row.get("GAME_ID") or "")
        if not game_id:
            skipped += 1
            continue
        wl = row.get("WL")
        pts = row.get("PTS")
        if wl not in ("W", "L") or pts is None:
            skipped += 1
            continue
        matchup = str(row.get("MATCHUP") or "")
        team_name = _normalize_team_name(str(row.get("TEAM_NAME") or ""))
        if not team_name:
            skipped += 1
            continue

        bucket = by_id[game_id]
        bucket.setdefault("game_id", game_id)
        bucket["date"] = _parse_game_date(str(row.get("GAME_DATE") or ""))
        if _is_home_matchup(matchup):
            bucket["home_team"] = team_name
            bucket["home_score"] = int(pts)
        else:
            bucket["away_team"] = team_name
            bucket["away_score"] = int(pts)

    games: list[ParsedGame] = []
    for game_id, bucket in by_id.items():
        required = ("date", "home_team", "away_team", "home_score", "away_score")
        if any(key not in bucket for key in required):
            skipped += 1
            continue
        if bucket["home_score"] == bucket["away_score"]:
            skipped += 1
            continue
        games.append(
            ParsedGame(
                game_id=game_id,
                date=bucket["date"],
                season=season,
                season_label=season_label,
                game_type=game_type,
                home_team=bucket["home_team"],
                away_team=bucket["away_team"],
                home_score=int(bucket["home_score"]),
                away_score=int(bucket["away_score"]),
            )
        )
    return games, skipped


def _season_end_year(season_label: str) -> int:
    """``2023-24`` -> ``2024``."""
    return 2000 + int(season_label.split("-")[1])


def _month_ranges_for_season(season_end: int) -> list[tuple[str, str]]:
    """MM/DD/YYYY windows for leaguegamefinder (Oct–Jun)."""
    start_year = season_end - 1
    months: list[tuple[int, int]] = [
        (10, start_year),
        (11, start_year),
        (12, start_year),
        (1, season_end),
        (2, season_end),
        (3, season_end),
        (4, season_end),
        (5, season_end),
        (6, season_end),
    ]
    ranges: list[tuple[str, str]] = []
    for month, year in months:
        last_day = calendar.monthrange(year, month)[1]
        date_from = f"{month:02d}/01/{year}"
        date_to = f"{month:02d}/{last_day:02d}/{year}"
        ranges.append((date_from, date_to))
    return ranges


def _fetch_game_finder(
    params: dict[str, str],
    *,
    context: str,
) -> list[dict[str, Any]]:
    """Call stats.nba.com leaguegamefinder via nba_api (browser-style session)."""
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            finder = leaguegamefinder.LeagueGameFinder(
                league_id_nullable=params["LeagueID"],
                season_nullable=params["Season"],
                season_type_nullable=params["SeasonType"],
                player_or_team_abbreviation=params["PlayerOrTeam"],
                date_from_nullable=params.get("DateFrom"),
                date_to_nullable=params.get("DateTo"),
            )
            frame = finder.get_data_frames()[0]
            if frame.empty:
                return []
            return frame.to_dict("records")
        except Exception as exc:
            last_error = exc
            wait = REQUEST_SLEEP_SECONDS * (attempt + 2)
            logger.warning(
                "NBA Stats fetch failed (%s attempt %s/%s): %s",
                context,
                attempt + 1,
                REQUEST_RETRIES,
                exc,
            )
            time.sleep(wait)
    raise RuntimeError(f"Could not fetch {context} from NBA Stats API") from last_error


def _fetch_season_type(
    season_label: str,
    season_type: str,
) -> list[dict[str, Any]]:
    season_end = _season_end_year(season_label)
    merged: list[dict[str, Any]] = []
    for date_from, date_to in _month_ranges_for_season(season_end):
        params = {
            "LeagueID": "00",
            "Season": season_label,
            "SeasonType": season_type,
            "PlayerOrTeam": "T",
            "DateFrom": date_from,
            "DateTo": date_to,
        }
        context = f"{season_label} {season_type} {date_from}-{date_to}"
        rows = _fetch_game_finder(params, context=context)
        if rows:
            merged.extend(rows)
        time.sleep(REQUEST_SLEEP_SECONDS)
    return merged


def fetch_raw_games() -> list[ParsedGame]:
    all_games: list[ParsedGame] = []
    total_skipped = 0

    for season, season_label in SEASONS:
        for season_type, game_type in (
            ("Regular Season", "regular"),
            ("Playoffs", "playoff"),
        ):
            logger.info("Fetching NBA %s %s...", season_label, season_type)
            rows = _fetch_season_type(season_label, season_type)
            parsed, skipped = _parse_team_rows(
                rows,
                season=season,
                season_label=season_label,
                game_type=game_type,
            )
            total_skipped += skipped
            logger.info(
                "Season %s %s: %s games (%s rows skipped/non-final)",
                season_label,
                game_type,
                len(parsed),
                skipped,
            )
            all_games.extend(parsed)
            time.sleep(REQUEST_SLEEP_SECONDS)

    seen: set[str] = set()
    unique: list[ParsedGame] = []
    for game in all_games:
        if game.game_id in seen:
            continue
        seen.add(game.game_id)
        unique.append(game)
    unique.sort(key=lambda g: (g.date, g.game_id))
    logger.info(
        "Total completed games: %s (skipped/incomplete rows: %s)",
        len(unique),
        total_skipped,
    )
    return unique


def _games_to_frame(games: list[ParsedGame]) -> pd.DataFrame:
    records = [
        {
            "game_id": g.game_id,
            "date": g.date,
            "season": g.season,
            "game_type": g.game_type,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "home_win": int(g.home_score > g.away_score),
        }
        for g in games
    ]
    return pd.DataFrame(records)


def _collect_rest_gaps(df: pd.DataFrame) -> list[int]:
    gaps: list[int] = []
    team_last: dict[tuple[str, int], datetime] = {}
    for row in df.sort_values(["date", "game_id"]).itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")
        season = int(row.season)
        for team in (row.home_team, row.away_team):
            key = (team, season)
            if key in team_last:
                gap = (game_date - team_last[key]).days
                if 1 <= gap <= MAX_REST_GAP_DAYS:
                    gaps.append(gap)
        team_last[(row.home_team, season)] = game_date
        team_last[(row.away_team, season)] = game_date
    return gaps


def _median_rest_fill(df: pd.DataFrame) -> float:
    gaps = _collect_rest_gaps(df)
    if not gaps:
        return DEFAULT_REST_FILL
    return float(np.median(gaps))


def _compute_rest_and_b2b(df: pd.DataFrame, rest_fill: float) -> pd.DataFrame:
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    team_last_season: dict[tuple[str, int], datetime] = {}
    team_last_calendar: dict[str, datetime] = {}

    home_rest: list[float] = []
    away_rest: list[float] = []
    home_b2b: list[int] = []
    away_b2b: list[int] = []

    for row in df.itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")
        season = int(row.season)

        home_b2b.append(
            1
            if row.home_team in team_last_calendar
            and (game_date - team_last_calendar[row.home_team]).days == 1
            else 0
        )
        away_b2b.append(
            1
            if row.away_team in team_last_calendar
            and (game_date - team_last_calendar[row.away_team]).days == 1
            else 0
        )

        for team, bucket in (
            (row.home_team, home_rest),
            (row.away_team, away_rest),
        ):
            key = (team, season)
            if key in team_last_season:
                gap = (game_date - team_last_season[key]).days
                bucket.append(float(gap) if gap <= MAX_REST_GAP_DAYS else rest_fill)
            else:
                bucket.append(rest_fill)

        team_last_season[(row.home_team, season)] = game_date
        team_last_season[(row.away_team, season)] = game_date
        team_last_calendar[row.home_team] = game_date
        team_last_calendar[row.away_team] = game_date

    df = df.copy()
    df["home_rest_days"] = home_rest
    df["away_rest_days"] = away_rest
    df["home_b2b"] = home_b2b
    df["away_b2b"] = away_b2b
    return df


def build_modeling_table() -> pd.DataFrame:
    raw = fetch_raw_games()
    if not raw:
        raise RuntimeError("No completed games returned from NBA Stats API")

    df = _games_to_frame(raw)
    rest_fill = _median_rest_fill(df)
    logger.info(
        "Rest-day imputation: rest_fill=%.2f (median in-season gap, else %.1f)",
        rest_fill,
        DEFAULT_REST_FILL,
    )
    df = _compute_rest_and_b2b(df, rest_fill)
    return df[NBA_GAMES_COLUMNS]


def _sql_row(row: pd.Series) -> tuple:
    values = []
    for col in NBA_GAMES_COLUMNS:
        val = row[col]
        if pd.isna(val):
            values.append(None)
        else:
            values.append(val)
    return tuple(values)


def write_outputs(df: pd.DataFrame) -> None:
    PROCESSED_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PROCESSED_PARQUET, index=False)
    df.to_csv(PROCESSED_CSV, index=False)

    conn = get_connection()
    try:
        ensure_nba_games_table(conn)
        conn.execute("DELETE FROM nba_games")
        placeholders = ", ".join("?" * len(NBA_GAMES_COLUMNS))
        conn.executemany(
            f"INSERT INTO nba_games ({', '.join(NBA_GAMES_COLUMNS)}) VALUES ({placeholders})",
            [_sql_row(row) for _, row in df.iterrows()],
        )
        conn.commit()
    finally:
        conn.close()


def run_ingest() -> pd.DataFrame:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    df = build_modeling_table()
    write_outputs(df)
    for season_end, season_label in SEASONS:
        sub = df[df["season"] == season_end]
        regular = int((sub["game_type"] == "regular").sum())
        playoff = int((sub["game_type"] == "playoff").sum())
        logger.info(
            "Season %s (%s): %s regular, %s playoff, %s total",
            season_end,
            season_label,
            regular,
            playoff,
            len(sub),
        )
    logger.info(
        "Wrote %s rows to %s and SQLite nba_games",
        len(df),
        PROCESSED_PARQUET,
    )
    return df
