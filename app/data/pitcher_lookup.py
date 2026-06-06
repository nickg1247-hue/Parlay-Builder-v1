"""Pitcher ERA/WHIP/IP lookup by name + season (ingested games + pybaseball BREF cache)."""

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
DEFAULT_WHIP = 1.30
DEFAULT_IP = 150.0


def _pitcher_key(name: str | None) -> str:
    if not name or not str(name).strip():
        return ""
    return str(name).lower().strip()


def _build_from_games(df: pd.DataFrame) -> pd.DataFrame:
    home = df[
        [
            "season",
            "home_starting_pitcher",
            "home_pitcher_era",
            "home_pitcher_fip",
        ]
    ].rename(
        columns={
            "home_starting_pitcher": "pitcher_name",
            "home_pitcher_era": "era",
            "home_pitcher_fip": "fip",
        }
    )
    away = df[
        [
            "season",
            "away_starting_pitcher",
            "away_pitcher_era",
            "away_pitcher_fip",
        ]
    ].rename(
        columns={
            "away_starting_pitcher": "pitcher_name",
            "away_pitcher_era": "era",
            "away_pitcher_fip": "fip",
        }
    )
    combined = pd.concat([home, away], ignore_index=True)
    combined = combined[combined["pitcher_name"].notna() & combined["era"].notna()]
    combined["pitcher_key"] = combined["pitcher_name"].map(_pitcher_key)
    combined["whip"] = pd.NA
    combined["ip"] = pd.NA
    agg = combined.groupby(["season", "pitcher_key"], as_index=False).agg(
        era=("era", "median"),
        fip=("fip", "median"),
        whip=("whip", "median"),
        ip=("ip", "median"),
    )
    return agg


def _parse_bref_ip(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text == "nan":
        return None
    if "." in text:
        whole, frac = text.split(".", 1)
        try:
            return float(int(whole)) + float(int(frac)) / 3.0
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


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
        if "WHIP" in stats.columns:
            stats["whip"] = pd.to_numeric(stats["WHIP"], errors="coerce")
        else:
            stats["whip"] = pd.NA
        ip_col = "IP" if "IP" in stats.columns else None
        if ip_col:
            stats["ip"] = stats[ip_col].map(_parse_bref_ip)
        else:
            stats["ip"] = pd.NA
        stats["fip"] = pd.NA
        stats["season"] = season
        stats["pitcher_key"] = stats["pitcher_name"].map(_pitcher_key)
        frames.append(stats[["season", "pitcher_key", "era", "fip", "whip", "ip"]])
    if not frames:
        return pd.DataFrame(columns=["season", "pitcher_key", "era", "fip", "whip", "ip"])
    return pd.concat(frames, ignore_index=True)


def rebuild_pitcher_lookup_cache() -> pd.DataFrame:
    games = load_games()
    from_games = _build_from_games(games)
    from_bref = _build_from_bref()
    combined = pd.concat([from_games, from_bref], ignore_index=True)
    lookup = combined.groupby(["season", "pitcher_key"], as_index=False).agg(
        era=("era", "median"),
        fip=("fip", "median"),
        whip=("whip", "median"),
        ip=("ip", "median"),
    )
    PITCHER_LOOKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    lookup.to_parquet(PITCHER_LOOKUP_PATH, index=False)
    get_pitcher_lookup_table.cache_clear()
    logger.info("Wrote pitcher lookup: %s rows", len(lookup))
    return lookup


@lru_cache(maxsize=1)
def get_pitcher_lookup_table() -> pd.DataFrame:
    if PITCHER_LOOKUP_PATH.exists():
        return pd.read_parquet(PITCHER_LOOKUP_PATH)
    return rebuild_pitcher_lookup_cache()


def lookup_pitcher_profile(
    pitcher_name: str | None,
    season: int,
    era_medians: dict[int | str, float],
    default_whip: float = DEFAULT_WHIP,
    default_ip: float = DEFAULT_IP,
) -> dict[str, float]:
    """Return era, whip, ip (and optional fip) for a starter."""
    fallback_era = float(era_medians.get(season, era_medians.get("default", 4.0)))
    key = _pitcher_key(pitcher_name)
    if not key:
        return {"era": fallback_era, "fip": None, "whip": default_whip, "ip": default_ip}

    table = get_pitcher_lookup_table()
    match = pd.DataFrame()
    for lookup_season in (season, season - 1, season - 2):
        match = table[
            (table["season"] == lookup_season) & (table["pitcher_key"] == key)
        ]
        if not match.empty:
            break
    if match.empty:
        return {"era": fallback_era, "fip": None, "whip": default_whip, "ip": default_ip}

    row = match.iloc[0]
    era = float(row["era"]) if pd.notna(row["era"]) else fallback_era
    fip = float(row["fip"]) if "fip" in row and pd.notna(row["fip"]) else None
    whip = float(row["whip"]) if "whip" in row and pd.notna(row["whip"]) else default_whip
    ip = float(row["ip"]) if "ip" in row and pd.notna(row["ip"]) else default_ip
    return {"era": era, "fip": fip, "whip": whip, "ip": ip}


def lookup_pitcher_rates(
    pitcher_name: str | None,
    season: int,
    era_medians: dict[int | str, float],
) -> tuple[float, float | None]:
    """Return (era, fip) for starter; fall back to season median ERA if unknown."""
    prof = lookup_pitcher_profile(pitcher_name, season, era_medians)
    return prof["era"], prof["fip"]
