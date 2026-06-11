"""Pitcher L5 form and team bullpen rollups from per-game pitching log."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT

PITCHER_GAME_LOG_PATH = PROJECT_ROOT / "data" / "processed" / "mlb_pitcher_game_log.parquet"

# Fallback when log empty or insufficient relief history (overridden from 2023–24 medians).
DEFAULT_BULLPEN_ERA_14D = 4.20
DEFAULT_BULLPEN_IP_3D = 2.5
BULLPEN_ERA_WINDOW_DAYS = 14
BULLPEN_IP_WINDOW_DAYS = 3
BULLPEN_MAX_RELIEF_APPEARANCES = 20
L5_MAX_STARTS = 5


def pitcher_key(name: str | None) -> str:
    if not name or not str(name).strip():
        return ""
    return str(name).lower().strip()


@lru_cache(maxsize=4)
def get_pitcher_game_log() -> pd.DataFrame:
    if not PITCHER_GAME_LOG_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(PITCHER_GAME_LOG_PATH)
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    return df


def _rates_from_lines(ip: float, er: float, hits: float, walks: float) -> tuple[float, float]:
    if ip <= 0:
        return 0.0, 0.0
    era = (er / ip) * 9.0
    whip = (hits + walks) / ip
    return float(era), float(whip)


def pitcher_last_n_starts(
    pitcher_name: str | None,
    before_date: pd.Timestamp,
    season: int,
    log: pd.DataFrame,
    n: int = L5_MAX_STARTS,
) -> pd.DataFrame:
    """
    Starter lines strictly before before_date.

    Prefer same-season starts; if fewer than n, include prior-season starts
    (still before before_date). Same-day games are never included.
    """
    key = pitcher_key(pitcher_name)
    if not key or log.empty:
        return pd.DataFrame()

    before = pd.to_datetime(before_date)
    subset = log[
        (log["pitcher_key"] == key)
        & (log["is_starter"])
        & (log["date"] < before)
    ].sort_values("date", ascending=False)

    same_season = subset[subset["season"] == season]
    if len(same_season) >= n:
        return same_season.head(n).copy()

    prior_season = subset[subset["season"] < season]
    combined = pd.concat([same_season, prior_season], ignore_index=True)
    return combined.sort_values("date", ascending=False).head(n).copy()


def pitcher_l5_rates(
    pitcher_name: str | None,
    before_date: pd.Timestamp,
    season: int,
    log: pd.DataFrame,
    n: int = L5_MAX_STARTS,
) -> dict[str, float] | None:
    """IP-weighted ERA and WHIP over last n starts; None if no qualifying starts."""
    starts = pitcher_last_n_starts(pitcher_name, before_date, season, log, n=n)
    if starts.empty:
        return None
    ip = float(starts["ip"].sum())
    if ip <= 0:
        return None
    era, whip = _rates_from_lines(
        ip,
        float(starts["er"].sum()),
        float(starts["hits"].sum()),
        float(starts["walks"].sum()),
    )
    return {"era": era, "whip": whip, "starts": len(starts)}


def _relief_window(
    team: str,
    before_date: pd.Timestamp,
    log: pd.DataFrame,
    window_days: int,
    max_appearances: int | None = None,
) -> pd.DataFrame:
    if log.empty:
        return pd.DataFrame()
    before = pd.to_datetime(before_date)
    start = before - pd.Timedelta(days=window_days)
    subset = log[
        (log["team"] == team)
        & (~log["is_starter"])
        & (log["date"] >= start)
        & (log["date"] < before)
    ].sort_values("date", ascending=False)
    if max_appearances is not None and len(subset) > max_appearances:
        return subset.head(max_appearances).copy()
    return subset


def team_bullpen_era_14d(
    team: str,
    before_date: pd.Timestamp,
    log: pd.DataFrame,
) -> float | None:
    """Bullpen ERA over prior 14 calendar days (max 20 relief appearances)."""
    window = _relief_window(
        team,
        before_date,
        log,
        BULLPEN_ERA_WINDOW_DAYS,
        max_appearances=BULLPEN_MAX_RELIEF_APPEARANCES,
    )
    if window.empty:
        return None
    ip = float(window["ip"].sum())
    if ip <= 0:
        return None
    era, _ = _rates_from_lines(ip, float(window["er"].sum()), 0.0, 0.0)
    return era


def team_bullpen_ip_3d(
    team: str,
    before_date: pd.Timestamp,
    log: pd.DataFrame,
) -> float | None:
    """Total bullpen innings in prior 3 calendar days."""
    window = _relief_window(team, before_date, log, BULLPEN_IP_WINDOW_DAYS)
    if window.empty:
        return None
    return float(window["ip"].sum())


@lru_cache(maxsize=1)
def bullpen_neutral_defaults() -> dict[str, float]:
    """League-neutral bullpen constants from 2023–2024 train medians when log exists."""
    log = get_pitcher_game_log()
    if log.empty:
        return {"era_14d": DEFAULT_BULLPEN_ERA_14D, "ip_3d": DEFAULT_BULLPEN_IP_3D}

    from app.models.mlb_baseline import load_games

    games = load_games()
    train = games[games["season"].isin([2023, 2024])]
    eras: list[float] = []
    ips: list[float] = []
    for row in train.itertuples(index=False):
        before = pd.to_datetime(row.date)
        for team in (row.home_team, row.away_team):
            era = team_bullpen_era_14d(team, before, log)
            ip3 = team_bullpen_ip_3d(team, before, log)
            if era is not None:
                eras.append(era)
            if ip3 is not None:
                ips.append(ip3)

    return {
        "era_14d": float(np.median(eras)) if eras else DEFAULT_BULLPEN_ERA_14D,
        "ip_3d": float(np.median(ips)) if ips else DEFAULT_BULLPEN_IP_3D,
    }


def team_bullpen_features(
    team: str,
    before_date: pd.Timestamp,
    log: pd.DataFrame,
) -> dict[str, float]:
    """Pregame bullpen ERA (14d) and IP (3d) with train-median fallbacks."""
    neutral = bullpen_neutral_defaults()
    era = team_bullpen_era_14d(team, before_date, log)
    ip3 = team_bullpen_ip_3d(team, before_date, log)
    return {
        "era_14d": era if era is not None else neutral["era_14d"],
        "ip_3d": ip3 if ip3 is not None else neutral["ip_3d"],
    }
