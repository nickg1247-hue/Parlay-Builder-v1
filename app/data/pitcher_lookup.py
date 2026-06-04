"""Pitcher ERA/FIP lookup by name + season (ingested games + pybaseball BREF cache)."""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
import pybaseball as pyb

from app.config import PROJECT_ROOT
from app.models.mlb_baseline import load_games

logger = logging.getLogger(__name__)

PITCHER_LOOKUP_PATH = PROJECT_ROOT / "data" / "processed" / "pitcher_rate_lookup.parquet"
BREF_SEASONS = (2023, 2024, 2025, 2026)


def _pitcher_key(name: str | None) -> str:
    if not name or not str(name).strip():
        return ""
    return str(name).lower().strip()


def _build_from_games(df: pd.DataFrame) -> pd.DataFrame:
    home = df[["season", "home_starting_pitcher", "home_pitcher_era", "home_pitcher_fip"]].rename(
        columns={
            "home_starting_pitcher": "pitcher_name",
            "home_pitcher_era": "era",
            "home_pitcher_fip": "fip",
        }
    )
    away = df[["season", "away_starting_pitcher", "away_pitcher_era", "away_pitcher_fip"]].rename(
        columns={
            "away_starting_pitcher": "pitcher_name",
            "away_pitcher_era": "era",
            "away_pitcher_fip": "fip",
        }
    )
    combined = pd.concat([home, away], ignore_index=True)
    combined = combined[combined["pitcher_name"].notna() & combined["era"].notna()]
    combined["pitcher_key"] = combined["pitcher_name"].map(_pitcher_key)
    agg = (
        combined.groupby(["season", "pitcher_key"], as_index=False)
        .agg(era=("era", "median"), fip=("fip", "median"))
    )
    return agg


def _build_from_bref() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    pyb.cache.enable()
    for season in BREF_SEASONS:
        try:
            stats = pyb.pitching_stats_bref(season)
        except Exception as exc:
            logger.warning("BREF pitching stats failed for %s: %s", season, exc)
            continue
        stats = stats.rename(columns={"Name": "pitcher_name", "ERA": "era"})
        stats["fip"] = pd.NA
        stats["season"] = season
        stats["pitcher_key"] = stats["pitcher_name"].map(_pitcher_key)
        frames.append(stats[["season", "pitcher_key", "era", "fip"]])
    if not frames:
        return pd.DataFrame(columns=["season", "pitcher_key", "era", "fip"])
    return pd.concat(frames, ignore_index=True)


def rebuild_pitcher_lookup_cache() -> pd.DataFrame:
    games = load_games()
    from_games = _build_from_games(games)
    from_bref = _build_from_bref()
    combined = pd.concat([from_games, from_bref], ignore_index=True)
    lookup = (
        combined.groupby(["season", "pitcher_key"], as_index=False)
        .agg(era=("era", "median"), fip=("fip", "median"))
    )
    PITCHER_LOOKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    lookup.to_parquet(PITCHER_LOOKUP_PATH, index=False)
    logger.info("Wrote pitcher lookup: %s rows", len(lookup))
    return lookup


@lru_cache(maxsize=1)
def get_pitcher_lookup_table() -> pd.DataFrame:
    if PITCHER_LOOKUP_PATH.exists():
        return pd.read_parquet(PITCHER_LOOKUP_PATH)
    return rebuild_pitcher_lookup_cache()


def lookup_pitcher_rates(
    pitcher_name: str | None,
    season: int,
    era_medians: dict[int | str, float],
) -> tuple[float, float | None]:
    """Return (era, fip) for starter; fall back to season median ERA if unknown."""
    fallback = float(era_medians.get(season, era_medians.get("default", 4.0)))
    key = _pitcher_key(pitcher_name)
    if not key:
        return fallback, None

    table = get_pitcher_lookup_table()
    match = table[(table["season"] == season) & (table["pitcher_key"] == key)]
    if match.empty:
        return fallback, None

    row = match.iloc[0]
    era = float(row["era"]) if pd.notna(row["era"]) else fallback
    fip = float(row["fip"]) if pd.notna(row["fip"]) else None
    return era, fip
