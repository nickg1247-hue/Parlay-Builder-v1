"""Free / offline NBA moneylines for market eval (no Odds API historical burn)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.nba_odds_repository import repository_odds_dataframe
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.team_aliases import is_valid_american_odds

logger = logging.getLogger(__name__)

# End-year season label (2026 = 2025-26 holdout).
HOLDOUT_SEASON_END = 2026
ODDS_2026_CSV = PROJECT_ROOT / "data" / "processed" / "nba_odds_2026.csv"

# Documented free sources (manual import — no live sportsbook scraping in this repo):
# - SportsBookReview archives: https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm
# - Community scrapers (e.g. flancast90/sportsbookreview-scraper) — export to CSV, then import below.


def load_csv_odds(path: Path | None = None) -> pd.DataFrame:
    """Load normalized moneyline CSV: date, home_team, away_team, home_ml, away_ml."""
    csv_path = path or ODDS_2026_CSV
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    required = {"date", "home_team", "away_team", "home_ml", "away_ml"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"NBA odds CSV missing columns: {sorted(missing)}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["home_team"] = df["home_team"].map(normalize_nba_team_name)
    df["away_team"] = df["away_team"].map(normalize_nba_team_name)
    valid = df.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    df = df[valid].copy()
    return df.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)


def import_csv(source: Path, dest: Path | None = None) -> Path:
    """Validate and copy user CSV to the canonical holdout odds path."""
    dest = dest or ODDS_2026_CSV
    df = load_csv_odds(source)
    if df.empty:
        raise ValueError(f"No valid odds rows in {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    logger.info("Wrote %s NBA odds rows to %s", len(df), dest)
    return dest


def load_odds_for_date(game_date: date) -> tuple[pd.DataFrame, str]:
    """
    Odds for one calendar date from CSV (priority) + nba_odds_repository only.

    Never calls The Odds API.
    """
    iso = game_date.isoformat()
    frames: list[pd.DataFrame] = []
    source = "none"

    csv_df = load_csv_odds()
    if not csv_df.empty:
        day = csv_df[csv_df["date"] == iso]
        if not day.empty:
            frames.append(day.assign(priority=0))
            source = "historical_cache"

    repo_df = repository_odds_dataframe({iso})
    if not repo_df.empty:
        frames.append(repo_df.assign(priority=1))
        if source == "none":
            source = "repository"

    if not frames:
        return pd.DataFrame(), "none"

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.sort_values(["priority", "date"]).drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    )
    out = merged.drop(columns=["priority"], errors="ignore").reset_index(drop=True)
    if source == "none" and not out.empty:
        source = "historical_cache"
    return out, source


def load_holdout_odds(holdout_dates: set[str] | None = None) -> pd.DataFrame:
    """
    Priority: free CSV, then live-captured nba_odds_repository snapshots.

    Never calls The Odds API (eval script stays offline).
    """
    frames: list[pd.DataFrame] = []
    csv_df = load_csv_odds()
    if not csv_df.empty:
        frames.append(csv_df.assign(priority=0))
    repo_df = repository_odds_dataframe(holdout_dates)
    if not repo_df.empty:
        frames.append(repo_df.assign(priority=1))
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.sort_values(["priority", "date"]).drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    )
    return merged.drop(columns=["priority"], errors="ignore").reset_index(drop=True)
