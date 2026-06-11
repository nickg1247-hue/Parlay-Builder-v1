"""CFB game ingest: CollegeFootballData.com API (FBS regular season)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.db.cfb_schema import CFB_GAMES_COLUMNS, ensure_cfb_games_table
from app.db.database import get_connection
from app.odds.cfb_team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

CFBD_BASE_URL = "https://api.collegefootballdata.com"
SEASONS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "cfb_games.parquet"
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "cfb_games.csv"
MAX_REST_GAP_DAYS = 21
DEFAULT_REST_FILL = 7.0
REQUEST_SLEEP_SECONDS = 0.6
REQUEST_RETRIES = 4


@dataclass
class ParsedGame:
    game_id: str
    date: str
    season: int
    game_type: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


def _require_api_key() -> str:
    key = (os.getenv("CFBD_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "CFBD_API_KEY is not set. Add your CollegeFootballData.com API key to .env "
            "(see .env.example). Get a free key at https://collegefootballdata.com/key"
        )
    return key


def _parse_game_date(raw: str) -> str:
    if not raw:
        return ""
    return raw[:10]


def _fetch_games_season(
    client: httpx.Client,
    season: int,
    *,
    api_key: str,
) -> list[dict[str, Any]]:
    params = {
        "year": str(season),
        "seasonType": "regular",
        "division": "fbs",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = client.get(
                f"{CFBD_BASE_URL}/games",
                params=params,
                headers=headers,
            )
            if response.status_code == 401:
                raise SystemExit(
                    "CFBD API returned 401 Unauthorized. Check CFBD_API_KEY in .env."
                )
            response.raise_for_status()
            data = response.json()
            return list(data) if isinstance(data, list) else []
        except SystemExit:
            raise
        except Exception as exc:
            last_error = exc
            wait = REQUEST_SLEEP_SECONDS * (attempt + 2)
            logger.warning(
                "CFBD fetch failed (season %s attempt %s/%s): %s",
                season,
                attempt + 1,
                REQUEST_RETRIES,
                exc,
            )
            time.sleep(wait)
    raise RuntimeError(f"Could not fetch CFBD games for season {season}") from last_error


def _is_fbs_relevant_game(row: dict[str, Any]) -> bool:
    """Keep FBS vs FBS and FBS vs FCS; drop FCS-only and lower-division matchups."""
    home_cls = str(row.get("homeClassification") or "").lower()
    away_cls = str(row.get("awayClassification") or "").lower()
    return home_cls == "fbs" or away_cls == "fbs"


def _parse_cfbd_row(row: dict[str, Any], season: int) -> ParsedGame | None:
    if not _is_fbs_relevant_game(row):
        return None
    if not row.get("completed"):
        return None
    home_pts = row.get("homePoints")
    away_pts = row.get("awayPoints")
    if home_pts is None or away_pts is None:
        return None
    home_score = int(home_pts)
    away_score = int(away_pts)
    if home_score == away_score:
        return None
    home_team = normalize_team_name(str(row.get("homeTeam") or ""))
    away_team = normalize_team_name(str(row.get("awayTeam") or ""))
    if not home_team or not away_team:
        return None
    game_id = str(row.get("id") or "")
    if not game_id:
        return None
    game_date = _parse_game_date(str(row.get("startDate") or ""))
    if not game_date:
        return None
    return ParsedGame(
        game_id=game_id,
        date=game_date,
        season=season,
        game_type="regular",
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
    )


def fetch_raw_games(*, api_key: str | None = None) -> list[ParsedGame]:
    """Pull all FBS regular-season games with one CFBD request per season (not per game/team)."""
    key = api_key or _require_api_key()
    all_games: list[ParsedGame] = []
    skipped = 0
    logger.info(
        "CFBD ingest: %d season-level GET /games requests (year + division=fbs filter)",
        len(SEASONS),
    )

    with httpx.Client(timeout=60.0) as client:
        for season in SEASONS:
            logger.info("Fetching CFBD FBS regular season %s...", season)
            rows = _fetch_games_season(client, season, api_key=key)
            season_games: list[ParsedGame] = []
            for row in rows:
                parsed = _parse_cfbd_row(row, season)
                if parsed is None:
                    skipped += 1
                    continue
                season_games.append(parsed)
            logger.info("Season %s: %s completed games (%s skipped)", season, len(season_games), skipped)
            all_games.extend(season_games)
            time.sleep(REQUEST_SLEEP_SECONDS)

    seen: set[str] = set()
    unique: list[ParsedGame] = []
    for game in all_games:
        if game.game_id in seen:
            continue
        seen.add(game.game_id)
        unique.append(game)
    unique.sort(key=lambda g: (g.date, g.game_id))
    logger.info("Total completed FBS games: %s", len(unique))
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


def build_modeling_table(*, api_key: str | None = None) -> pd.DataFrame:
    raw = fetch_raw_games(api_key=api_key)
    if not raw:
        raise RuntimeError("No completed games returned from CFBD API")

    df = _games_to_frame(raw)
    rest_fill = _median_rest_fill(df)
    logger.info(
        "Rest-day imputation: rest_fill=%.2f (median in-season gap, else %.1f)",
        rest_fill,
        DEFAULT_REST_FILL,
    )
    df = _compute_rest_and_b2b(df, rest_fill)
    return df[CFB_GAMES_COLUMNS]


def _sql_row(row: pd.Series) -> tuple:
    values = []
    for col in CFB_GAMES_COLUMNS:
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
        ensure_cfb_games_table(conn)
        conn.execute("DELETE FROM cfb_games")
        placeholders = ", ".join("?" * len(CFB_GAMES_COLUMNS))
        conn.executemany(
            f"INSERT INTO cfb_games ({', '.join(CFB_GAMES_COLUMNS)}) VALUES ({placeholders})",
            [_sql_row(row) for _, row in df.iterrows()],
        )
        conn.commit()
    finally:
        conn.close()


def run_ingest(*, api_key: str | None = None) -> pd.DataFrame:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    df = build_modeling_table(api_key=api_key)
    write_outputs(df)
    for season in SEASONS:
        sub = df[df["season"] == season]
        logger.info("Season %s: %s games", season, len(sub))
    logger.info(
        "Wrote %s rows to %s and SQLite cfb_games",
        len(df),
        PROCESSED_PARQUET,
    )
    return df
