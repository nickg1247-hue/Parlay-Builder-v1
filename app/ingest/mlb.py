"""MLB game ingest: MLB Stats API (games/scores/pitchers) + pybaseball (ERA/FIP)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pandas as pd
import pybaseball as pyb

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.mlb_schema import MLB_GAMES_COLUMNS, ensure_mlb_games_table

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
MLB_PITCHING_STATS_URL = "https://statsapi.mlb.com/api/v1/stats"
SEASONS = (2023, 2024, 2025, 2026)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "mlb_games.parquet"
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "mlb_games.csv"
PITCHER_CACHE = PROJECT_ROOT / "data" / "processed" / "mlb_pitcher_cache.parquet"
PITCHING_STATS_CACHE = PROJECT_ROOT / "data" / "processed" / "mlb_pitching_stats_cache.parquet"
PITCHING_STATS_RETRIES = 4
BOXSCORE_WORKERS = 12
BOXSCORE_RETRIES = 3


@dataclass
class RawGame:
    game_id: str
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


def _normalize_team_name(name: str) -> str:
    return name.strip()


def _fetch_season_schedule(client: httpx.Client, season: int) -> list[dict[str, Any]]:
    params = {
        "sportId": 1,
        "season": season,
        "gameType": "R",
        "hydrate": "linescore",
    }
    response = client.get(MLB_SCHEDULE_URL, params=params, timeout=60.0)
    response.raise_for_status()
    games: list[dict[str, Any]] = []
    for day in response.json().get("dates", []):
        games.extend(day.get("games", []))
    return games


def _parse_final_games(api_games: list[dict[str, Any]]) -> list[RawGame]:
    rows: list[RawGame] = []
    for game in api_games:
        status = game.get("status", {})
        if status.get("abstractGameState") != "Final":
            continue
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        home_score = home.get("score")
        away_score = away.get("score")
        if home_score is None or away_score is None:
            continue
        rows.append(
            RawGame(
                game_id=str(game["gamePk"]),
                date=game.get("officialDate") or game["gameDate"][:10],
                home_team=_normalize_team_name(home["team"]["name"]),
                away_team=_normalize_team_name(away["team"]["name"]),
                home_score=int(home_score),
                away_score=int(away_score),
            )
        )
    return rows


def _starter_from_boxscore(team_side: dict[str, Any]) -> str | None:
    players = team_side.get("players", {})
    for pid in team_side.get("pitchers", []):
        key = f"ID{pid}" if not str(pid).startswith("ID") else str(pid)
        if key not in players:
            key = str(pid)
        player = players.get(key, {})
        pitching = player.get("stats", {}).get("pitching", {})
        if pitching.get("gamesStarted", 0) >= 1 or pitching.get("inningsPitched", "0") not in (
            "0",
            "0.0",
            None,
            "",
        ):
            name = player.get("person", {}).get("fullName")
            if name:
                return name
    pitchers = team_side.get("pitchers", [])
    if pitchers:
        key = f"ID{pitchers[0]}"
        player = players.get(key, {})
        return player.get("person", {}).get("fullName")
    return None


def _fetch_starting_pitchers(game_pk: str) -> tuple[str | None, str | None]:
    url = MLB_BOXSCORE_URL.format(game_pk=game_pk)
    for attempt in range(BOXSCORE_RETRIES):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
            home = _starter_from_boxscore(data["teams"]["home"])
            away = _starter_from_boxscore(data["teams"]["away"])
            return home, away
        except Exception:
            if attempt == BOXSCORE_RETRIES - 1:
                return None, None
            time.sleep(0.5 * (attempt + 1))
    return None, None


def _load_pitcher_cache(game_ids: set[str]) -> dict[str, tuple[str | None, str | None]]:
    if not PITCHER_CACHE.exists():
        return {}
    cache_df = pd.read_parquet(PITCHER_CACHE)
    if not game_ids.issubset(set(cache_df["game_id"].astype(str))):
        return {}
    logger.info("Using cached starting pitchers from %s", PITCHER_CACHE)
    return {
        str(row.game_id): (row.home_starting_pitcher, row.away_starting_pitcher)
        for row in cache_df.itertuples(index=False)
    }


def _save_pitcher_cache(pitchers: dict[str, tuple[str | None, str | None]]) -> None:
    PITCHER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "game_id": gid,
            "home_starting_pitcher": home,
            "away_starting_pitcher": away,
        }
        for gid, (home, away) in pitchers.items()
    ]
    pd.DataFrame(rows).to_parquet(PITCHER_CACHE, index=False)


def _attach_starting_pitchers(games: list[RawGame]) -> pd.DataFrame:
    records = []
    total = len(games)
    game_ids = {g.game_id for g in games}
    pitchers: dict[str, tuple[str | None, str | None]] = _load_pitcher_cache(game_ids)

    if len(pitchers) < len(games):
        missing = [g for g in games if g.game_id not in pitchers]
        logger.info("Fetching boxscores for %s games...", len(missing))
        with ThreadPoolExecutor(max_workers=BOXSCORE_WORKERS) as pool:
            futures = {
                pool.submit(_fetch_starting_pitchers, g.game_id): g.game_id
                for g in missing
            }
            done = 0
            for future in as_completed(futures):
                game_id = futures[future]
                pitchers[game_id] = future.result()
                done += 1
                if done % 250 == 0 or done == len(missing):
                    logger.info("Boxscores fetched: %s / %s", done, len(missing))
        _save_pitcher_cache(pitchers)

    for g in games:
        home_p, away_p = pitchers.get(g.game_id, (None, None))
        records.append(
            {
                "game_id": g.game_id,
                "date": g.date,
                "home_team": g.home_team,
                "away_team": g.away_team,
                "home_score": g.home_score,
                "away_score": g.away_score,
                "home_win": int(g.home_score > g.away_score),
                "home_starting_pitcher": home_p,
                "away_starting_pitcher": away_p,
            }
        )
    return pd.DataFrame(records)


def _cached_pitching_stats(season: int) -> pd.DataFrame | None:
    if not PITCHING_STATS_CACHE.exists():
        return None
    cached = pd.read_parquet(PITCHING_STATS_CACHE)
    season_rows = cached[cached["season"] == season]
    if season_rows.empty:
        return None
    return season_rows[["pitcher_key", "era", "fip", "season"]].copy()


def _save_pitching_stats_cache(frame: pd.DataFrame) -> None:
    PITCHING_STATS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if PITCHING_STATS_CACHE.exists():
        existing = pd.read_parquet(PITCHING_STATS_CACHE)
        existing = existing[existing["season"] != frame["season"].iloc[0]]
        frame = pd.concat([existing, frame], ignore_index=True)
    frame.to_parquet(PITCHING_STATS_CACHE, index=False)


def _parse_mlb_era(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-.--", "-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_pitching_stats_mlb_api(season: int) -> pd.DataFrame:
    """Season pitching ERA from MLB Stats API (replaces flaky pybaseball BREF scrape)."""
    params = {
        "stats": "season",
        "group": "pitching",
        "season": season,
        "sportId": 1,
        "playerPool": "ALL",
        "limit": 5000,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.get(MLB_PITCHING_STATS_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    splits = payload.get("stats", [{}])[0].get("splits", [])
    rows: list[dict[str, Any]] = []
    for split in splits:
        name = split.get("player", {}).get("fullName")
        era = _parse_mlb_era(split.get("stat", {}).get("era"))
        if not name or era is None:
            continue
        rows.append(
            {
                "pitcher_key": str(name).lower().strip(),
                "era": era,
                "fip": pd.NA,
                "season": season,
            }
        )
    if not rows:
        raise RuntimeError(f"MLB Stats API returned no pitching rows for {season}")
    return pd.DataFrame(rows)


def _load_pitching_stats_bref(season: int) -> pd.DataFrame:
    """Baseball Reference via pybaseball — fallback when MLB API unavailable."""
    pyb.cache.enable()
    stats = pyb.pitching_stats_bref(season)
    stats = stats.rename(columns={"Name": "pitcher_name", "ERA": "era"})
    stats["fip"] = pd.NA
    stats["pitcher_key"] = stats["pitcher_name"].str.lower().str.strip()
    stats["season"] = season
    return stats[["pitcher_key", "era", "fip", "season"]]


def _load_pitching_stats(season: int) -> pd.DataFrame:
    cached = _cached_pitching_stats(season)
    loaders = (
        ("MLB Stats API", _load_pitching_stats_mlb_api),
        ("pybaseball BREF", _load_pitching_stats_bref),
    )
    last_error: Exception | None = None
    for source_name, loader in loaders:
        for attempt in range(PITCHING_STATS_RETRIES):
            try:
                out = loader(season)
                _save_pitching_stats_cache(out)
                logger.info(
                    "Loaded %s pitching rows for %s from %s",
                    len(out),
                    season,
                    source_name,
                )
                return out
            except Exception as exc:
                last_error = exc
                wait = 2.0 * (attempt + 1)
                logger.warning(
                    "%s pitching stats (%s) failed (attempt %s/%s): %s",
                    source_name,
                    season,
                    attempt + 1,
                    PITCHING_STATS_RETRIES,
                    exc,
                )
                time.sleep(wait)

    if cached is not None:
        logger.warning("Using cached pitching stats for %s", season)
        return cached

    raise RuntimeError(
        f"Could not load pitching stats for {season}"
    ) from last_error


def _attach_pitcher_rates(df: pd.DataFrame) -> pd.DataFrame:
    era_frames = [_load_pitching_stats(season) for season in SEASONS]
    era_lookup = pd.concat(era_frames, ignore_index=True)
    era_lookup = era_lookup.drop_duplicates(subset=["pitcher_key", "season"], keep="first")
    df = df.copy()
    df["season"] = pd.to_datetime(df["date"]).dt.year

    for side in ("home", "away"):
        key = f"{side}_pitcher_key"
        df[key] = df[f"{side}_starting_pitcher"].str.lower().str.strip()
        merged = df[[key, "season"]].merge(
            era_lookup,
            left_on=[key, "season"],
            right_on=["pitcher_key", "season"],
            how="left",
        )
        df[f"{side}_pitcher_era"] = merged["era"].values
        df[f"{side}_pitcher_fip"] = merged["fip"].values
        df.drop(columns=[key], inplace=True)

    df.drop(columns=["season"], inplace=True)
    return df


def _last_n_metrics(history: list[tuple[int, int]], n: int = 10) -> tuple[float | None, float | None]:
    if not history:
        return None, None
    window = history[-n:]
    wins = [w for w, _ in window]
    diffs = [d for _, d in window]
    return sum(wins) / len(window), sum(diffs) / len(diffs)


def _compute_rolling_and_rest(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    team_history: dict[str, list[tuple[int, int]]] = defaultdict(list)
    team_last_date: dict[str, datetime] = {}

    home_l10_wp: list[float | None] = []
    away_l10_wp: list[float | None] = []
    home_l10_rd: list[float | None] = []
    away_l10_rd: list[float | None] = []
    home_rest: list[int | None] = []
    away_rest: list[int | None] = []

    for row in df.itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")

        h_wp, h_rd = _last_n_metrics(team_history[row.home_team])
        a_wp, a_rd = _last_n_metrics(team_history[row.away_team])
        home_l10_wp.append(h_wp)
        away_l10_wp.append(a_wp)
        home_l10_rd.append(h_rd)
        away_l10_rd.append(a_rd)

        home_rest.append(
            (game_date - team_last_date[row.home_team]).days
            if row.home_team in team_last_date
            else None
        )
        away_rest.append(
            (game_date - team_last_date[row.away_team]).days
            if row.away_team in team_last_date
            else None
        )

        home_win = 1 if row.home_score > row.away_score else 0
        away_win = 1 - home_win
        home_rd = row.home_score - row.away_score
        away_rd = row.away_score - row.home_score

        team_history[row.home_team].append((home_win, home_rd))
        team_history[row.away_team].append((away_win, away_rd))
        team_last_date[row.home_team] = game_date
        team_last_date[row.away_team] = game_date

    df["home_last10_win_pct"] = home_l10_wp
    df["away_last10_win_pct"] = away_l10_wp
    df["home_last10_run_diff"] = home_l10_rd
    df["away_last10_run_diff"] = away_l10_rd
    df["home_rest_days"] = home_rest
    df["away_rest_days"] = away_rest
    return df


def fetch_raw_games() -> list[RawGame]:
    all_games: list[RawGame] = []
    with httpx.Client() as client:
        for season in SEASONS:
            logger.info("Fetching MLB schedule for %s...", season)
            api_games = _fetch_season_schedule(client, season)
            parsed = _parse_final_games(api_games)
            logger.info("Season %s: %s completed games", season, len(parsed))
            all_games.extend(parsed)

    seen: set[str] = set()
    unique: list[RawGame] = []
    for g in all_games:
        if g.game_id in seen:
            continue
        seen.add(g.game_id)
        unique.append(g)
    unique.sort(key=lambda g: (g.date, g.game_id))
    return unique


def build_modeling_table() -> pd.DataFrame:
    raw = fetch_raw_games()
    if not raw:
        raise RuntimeError("No completed games returned from MLB Stats API")

    logger.info("Fetching starting pitchers for %s games...", len(raw))
    df = _attach_starting_pitchers(raw)
    logger.info("Loading pitcher ERA from MLB Stats API...")
    df = _attach_pitcher_rates(df)
    logger.info("Computing rolling features and rest days...")
    df = _compute_rolling_and_rest(df)
    df["total_runs"] = df["home_score"] + df["away_score"]
    return df[MLB_GAMES_COLUMNS]


def _sql_row(row: pd.Series) -> tuple:
    values = []
    for col in MLB_GAMES_COLUMNS:
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
        ensure_mlb_games_table(conn)
        conn.execute("DELETE FROM mlb_games")
        placeholders = ", ".join("?" * len(MLB_GAMES_COLUMNS))
        conn.executemany(
            f"INSERT INTO mlb_games ({', '.join(MLB_GAMES_COLUMNS)}) VALUES ({placeholders})",
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
    logger.info(
        "Wrote %s rows to %s and SQLite mlb_games",
        len(df),
        PROCESSED_PARQUET,
    )
    return df
