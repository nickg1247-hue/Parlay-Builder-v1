"""NFL game ingest via ESPN week-level scoreboard (preseason + regular)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.nfl_schema import NFL_GAMES_COLUMNS, ensure_nfl_games_table

logger = logging.getLogger(__name__)

ESPN_NFL_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
SEASONS: tuple[int, ...] = (2019, 2020, 2021, 2022, 2023, 2024, 2025)
PRESEASON_TYPE = 1
REGULAR_SEASON_TYPE = 2
ALLOWED_SEASON_TYPES = {PRESEASON_TYPE, REGULAR_SEASON_TYPE}
MAX_PRESEASON_WEEK = 4
MAX_WEEK = 18
SEASON_PULLS: tuple[tuple[int, int], ...] = (
    (PRESEASON_TYPE, MAX_PRESEASON_WEEK),
    (REGULAR_SEASON_TYPE, MAX_WEEK),
)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "nfl_games.parquet"
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "nfl_games.csv"
MAX_REST_GAP_DAYS = 21
DEFAULT_REST_FILL = 7.0
REQUEST_SLEEP_SECONDS = 0.2
REQUEST_RETRIES = 4

# Franchise continuity for Elo / rest / division (ESPN abbr changes).
ABBR_ALIASES = {
    "OAK": "LV",
    "WAS": "WSH",
    "WFT": "WSH",
    "LA": "LAR",
}

NFL_DIVISIONS: dict[str, str] = {
    "BUF": "AFC_EAST",
    "MIA": "AFC_EAST",
    "NE": "AFC_EAST",
    "NYJ": "AFC_EAST",
    "BAL": "AFC_NORTH",
    "CIN": "AFC_NORTH",
    "CLE": "AFC_NORTH",
    "PIT": "AFC_NORTH",
    "HOU": "AFC_SOUTH",
    "IND": "AFC_SOUTH",
    "JAX": "AFC_SOUTH",
    "TEN": "AFC_SOUTH",
    "DEN": "AFC_WEST",
    "KC": "AFC_WEST",
    "LAC": "AFC_WEST",
    "LV": "AFC_WEST",
    "DAL": "NFC_EAST",
    "NYG": "NFC_EAST",
    "PHI": "NFC_EAST",
    "WSH": "NFC_EAST",
    "CHI": "NFC_NORTH",
    "DET": "NFC_NORTH",
    "GB": "NFC_NORTH",
    "MIN": "NFC_NORTH",
    "ATL": "NFC_SOUTH",
    "CAR": "NFC_SOUTH",
    "NO": "NFC_SOUTH",
    "TB": "NFC_SOUTH",
    "ARI": "NFC_WEST",
    "LAR": "NFC_WEST",
    "SF": "NFC_WEST",
    "SEA": "NFC_WEST",
}


@dataclass
class ParsedGame:
    game_id: str
    date: str
    season: int
    week: int
    game_type: str
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    home_team_abbr: str
    away_team_abbr: str
    home_score: int
    away_score: int
    divisional: int = 0
    neutral_site: int = 0
    espn_home_ml: int | None = None
    espn_away_ml: int | None = None
    espn_spread: float | None = None
    espn_ou: float | None = None


def normalize_abbr(abbr: str | None) -> str:
    raw = str(abbr or "").strip().upper()
    return ABBR_ALIASES.get(raw, raw)


def team_division(abbr: str | None) -> str:
    return NFL_DIVISIONS.get(normalize_abbr(abbr), "")


def is_divisional(home_abbr: str | None, away_abbr: str | None) -> int:
    home_div = team_division(home_abbr)
    away_div = team_division(away_abbr)
    if not home_div or not away_div:
        return 0
    return int(home_div == away_div)


def _parse_game_date(raw: str) -> str:
    if not raw:
        return ""
    return raw[:10]


def _season_type(event: dict[str, Any], competition: dict[str, Any]) -> int | None:
    for blob in (event.get("season"), competition.get("season")):
        if isinstance(blob, dict) and blob.get("type") is not None:
            try:
                return int(blob["type"])
            except (TypeError, ValueError):
                return None
    return None


def _event_week(event: dict[str, Any], fallback: int = 0) -> int:
    week = event.get("week") or {}
    raw = week.get("number") if isinstance(week, dict) else None
    try:
        return int(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _event_season_year(event: dict[str, Any], fallback: int) -> int:
    season = event.get("season") or {}
    raw = season.get("year") if isinstance(season, dict) else None
    try:
        return int(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _competitor(competition: dict[str, Any], side: str) -> dict[str, Any]:
    competitors = competition.get("competitors") or []
    return next((c for c in competitors if c.get("homeAway") == side), {})


def parse_espn_event(
    event: dict[str, Any],
    *,
    season: int | None = None,
    week: int | None = None,
) -> ParsedGame | None:
    """Parse one ESPN scoreboard event into a completed preseason or regular game."""
    game_id = str(event.get("id") or "")
    if not game_id:
        return None
    competition = (event.get("competitions") or [{}])[0]
    season_type = _season_type(event, competition)
    if season_type is not None and season_type not in ALLOWED_SEASON_TYPES:
        return None
    game_type = "preseason" if season_type == PRESEASON_TYPE else "regular"
    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    completed = bool(status_type.get("completed")) or status_type.get("state") == "post"
    if not completed:
        return None
    home = _competitor(competition, "home")
    away = _competitor(competition, "away")
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    try:
        home_score = int(home.get("score"))
        away_score = int(away.get("score"))
    except (TypeError, ValueError):
        return None
    if home_score == away_score:
        return None
    home_name = str(home_team.get("displayName") or home_team.get("name") or "").strip()
    away_name = str(away_team.get("displayName") or away_team.get("name") or "").strip()
    if not home_name or not away_name:
        return None
    home_abbr = normalize_abbr(home_team.get("abbreviation"))
    away_abbr = normalize_abbr(away_team.get("abbreviation"))
    if not home_abbr or not away_abbr:
        return None
    game_date = _parse_game_date(str(event.get("date") or competition.get("date") or ""))
    if not game_date:
        return None
    season_year = _event_season_year(event, season if season is not None else int(game_date[:4]))
    week_num = _event_week(event, week or 0)
    neutral = 1 if competition.get("neutralSite") else 0
    espn_odds = _parse_espn_odds(competition)
    return ParsedGame(
        game_id=game_id,
        date=game_date,
        season=season_year,
        week=week_num,
        game_type=game_type,
        home_team=home_name,
        away_team=away_name,
        home_team_id=str(home_team.get("id") or ""),
        away_team_id=str(away_team.get("id") or ""),
        home_team_abbr=home_abbr,
        away_team_abbr=away_abbr,
        home_score=home_score,
        away_score=away_score,
        divisional=is_divisional(home_abbr, away_abbr),
        neutral_site=neutral,
        espn_home_ml=espn_odds.get("espn_home_ml"),
        espn_away_ml=espn_odds.get("espn_away_ml"),
        espn_spread=espn_odds.get("espn_spread"),
        espn_ou=espn_odds.get("espn_ou"),
    )


def parse_espn_schedule_event(
    event: dict[str, Any],
    *,
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any] | None:
    """Parse a regular-season ESPN event, including games that have not been played."""
    game_id = str(event.get("id") or "")
    if not game_id:
        return None
    competition = (event.get("competitions") or [{}])[0]
    season_type = _season_type(event, competition)
    if season_type is not None and season_type != REGULAR_SEASON_TYPE:
        return None
    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    completed = bool(status_type.get("completed")) or status_type.get("state") == "post"
    home = _competitor(competition, "home")
    away = _competitor(competition, "away")
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    home_name = str(home_team.get("displayName") or home_team.get("name") or "").strip()
    away_name = str(away_team.get("displayName") or away_team.get("name") or "").strip()
    if not home_name or not away_name:
        return None
    home_abbr = normalize_abbr(home_team.get("abbreviation"))
    away_abbr = normalize_abbr(away_team.get("abbreviation"))
    if not home_abbr or not away_abbr:
        return None
    game_date = _parse_game_date(str(event.get("date") or competition.get("date") or ""))
    if not game_date:
        return None
    season_year = _event_season_year(
        event, season if season is not None else int(game_date[:4])
    )
    week_num = _event_week(event, week or 0)
    home_score = None
    away_score = None
    home_win = None
    tie = False
    if completed:
        try:
            home_score = int(home.get("score"))
            away_score = int(away.get("score"))
        except (TypeError, ValueError):
            completed = False
            home_score = None
            away_score = None
        else:
            if home_score == away_score:
                tie = True
            else:
                home_win = int(home_score > away_score)
    return {
        "game_id": game_id,
        "date": game_date,
        "season": season_year,
        "week": week_num,
        "game_type": "regular",
        "home_team": home_name,
        "away_team": away_name,
        "home_team_id": str(home_team.get("id") or ""),
        "away_team_id": str(away_team.get("id") or ""),
        "home_team_abbr": home_abbr,
        "away_team_abbr": away_abbr,
        "home_logo_url": home_team.get("logo"),
        "away_logo_url": away_team.get("logo"),
        "divisional": is_divisional(home_abbr, away_abbr),
        "neutral_site": 1 if competition.get("neutralSite") else 0,
        "completed": completed,
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
        "tie": tie,
    }


def _parse_espn_odds(competition: dict[str, Any]) -> dict[str, Any]:
    """Best-effort ESPN BET lines from the same scoreboard payload (no extra request)."""
    odds_list = competition.get("odds") or []
    if not odds_list or not isinstance(odds_list, list):
        return {}
    blob = odds_list[0] if isinstance(odds_list[0], dict) else {}
    home = blob.get("homeTeamOdds") or {}
    away = blob.get("awayTeamOdds") or {}

    def _int(val: Any) -> int | None:
        try:
            if val is None or val == "":
                return None
            return int(val)
        except (TypeError, ValueError):
            return None

    def _float(val: Any) -> float | None:
        try:
            if val is None or val == "":
                return None
            return float(val)
        except (TypeError, ValueError):
            return None

    return {
        "espn_home_ml": _int(home.get("moneyLine")),
        "espn_away_ml": _int(away.get("moneyLine")),
        "espn_spread": _float(blob.get("spread")),
        "espn_ou": _float(blob.get("overUnder")),
    }


def _fetch_week(
    client: httpx.Client,
    season: int,
    week: int,
    season_type: int = REGULAR_SEASON_TYPE,
) -> list[dict[str, Any]]:
    params = {
        "dates": str(season),
        "seasontype": str(season_type),
        "week": str(week),
    }
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = client.get(ESPN_NFL_SCOREBOARD, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            return list(data.get("events") or [])
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            wait = REQUEST_SLEEP_SECONDS * (attempt + 2)
            logger.warning(
                "ESPN NFL week fetch failed (season %s type %s week %s attempt %s/%s): %s",
                season,
                season_type,
                week,
                attempt + 1,
                REQUEST_RETRIES,
                exc,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"Could not fetch ESPN NFL scoreboard for {season} type {season_type} week {week}"
    ) from last_error


def fetch_raw_games() -> list[ParsedGame]:
    """Pull completed preseason + regular-season games — one ESPN request per week."""
    all_games: list[ParsedGame] = []
    pulls = sum(weeks for _stype, weeks in SEASON_PULLS)
    logger.info(
        "ESPN NFL ingest: %d seasons × %d week scoreboard pulls (preseason + regular)",
        len(SEASONS),
        pulls,
    )
    with httpx.Client(timeout=60.0) as client:
        for season in SEASONS:
            season_games: list[ParsedGame] = []
            empty_weeks = 0
            for season_type, max_week in SEASON_PULLS:
                for week in range(1, max_week + 1):
                    events = _fetch_week(client, season, week, season_type)
                    parsed_week = 0
                    for event in events:
                        parsed = parse_espn_event(event, season=season, week=week)
                        if parsed is None:
                            continue
                        season_games.append(parsed)
                        parsed_week += 1
                    if parsed_week == 0:
                        empty_weeks += 1
                    time.sleep(REQUEST_SLEEP_SECONDS)
            logger.info(
                "Season %s: %s completed games (%s empty weeks)",
                season,
                len(season_games),
                empty_weeks,
            )
            all_games.extend(season_games)

    seen: set[str] = set()
    unique: list[ParsedGame] = []
    for game in all_games:
        if game.game_id in seen:
            continue
        seen.add(game.game_id)
        unique.append(game)
    unique.sort(key=lambda g: (g.date, g.game_id))
    logger.info("Total completed NFL preseason + regular-season games: %s", len(unique))
    return unique


def _games_to_frame(games: list[ParsedGame]) -> pd.DataFrame:
    records = [
        {
            "game_id": g.game_id,
            "date": g.date,
            "season": g.season,
            "week": int(g.week),
            "game_type": g.game_type,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_team_abbr": g.home_team_abbr,
            "away_team_abbr": g.away_team_abbr,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "home_win": int(g.home_score > g.away_score),
            "divisional": int(g.divisional),
            "neutral_site": int(g.neutral_site),
            "espn_home_ml": g.espn_home_ml,
            "espn_away_ml": g.espn_away_ml,
            "espn_spread": g.espn_spread,
            "espn_ou": g.espn_ou,
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
        for team in (row.home_team_abbr, row.away_team_abbr):
            key = (team, season)
            if key in team_last:
                gap = (game_date - team_last[key]).days
                if 1 <= gap <= MAX_REST_GAP_DAYS:
                    gaps.append(gap)
        team_last[(row.home_team_abbr, season)] = game_date
        team_last[(row.away_team_abbr, season)] = game_date
    return gaps


def _median_rest_fill(df: pd.DataFrame) -> float:
    gaps = _collect_rest_gaps(df)
    if not gaps:
        return DEFAULT_REST_FILL
    return float(np.median(gaps))


def _compute_rest(df: pd.DataFrame, rest_fill: float) -> pd.DataFrame:
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    team_last_season: dict[tuple[str, int], datetime] = {}
    home_rest: list[float] = []
    away_rest: list[float] = []

    for row in df.itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")
        season = int(row.season)
        for team, bucket in (
            (row.home_team_abbr, home_rest),
            (row.away_team_abbr, away_rest),
        ):
            key = (team, season)
            if key in team_last_season:
                gap = (game_date - team_last_season[key]).days
                bucket.append(float(gap) if 1 <= gap <= MAX_REST_GAP_DAYS else rest_fill)
            else:
                bucket.append(rest_fill)
        team_last_season[(row.home_team_abbr, season)] = game_date
        team_last_season[(row.away_team_abbr, season)] = game_date

    df = df.copy()
    df["home_rest_days"] = home_rest
    df["away_rest_days"] = away_rest
    return df


def build_modeling_table(games: list[ParsedGame] | None = None) -> pd.DataFrame:
    raw = games if games is not None else fetch_raw_games()
    if not raw:
        raise RuntimeError("No completed NFL games returned from ESPN scoreboard")
    df = _games_to_frame(raw)
    rest_fill = _median_rest_fill(df)
    logger.info(
        "Rest-day imputation: rest_fill=%.2f (median in-season gap, else %.1f)",
        rest_fill,
        DEFAULT_REST_FILL,
    )
    df = _compute_rest(df, rest_fill)
    return df[NFL_GAMES_COLUMNS]


def _sql_row(row: pd.Series) -> tuple:
    values = []
    for col in NFL_GAMES_COLUMNS:
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
        ensure_nfl_games_table(conn)
        conn.execute("DELETE FROM nfl_games")
        placeholders = ", ".join("?" * len(NFL_GAMES_COLUMNS))
        conn.executemany(
            f"INSERT INTO nfl_games ({', '.join(NFL_GAMES_COLUMNS)}) VALUES ({placeholders})",
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
    for season in SEASONS:
        sub = df[df["season"] == season]
        logger.info("Season %s: %s games", season, len(sub))
    logger.info(
        "Wrote %s rows to %s and SQLite nfl_games",
        len(df),
        PROCESSED_PARQUET,
    )
    return df
