"""Free / offline UFC moneylines for market eval (no Odds API historical burn)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.team_aliases import is_valid_american_odds
from app.odds.ufc_fighter_aliases import normalize_fighter_name
from app.odds.ufc_odds_convert import CANONICAL_COLUMNS, convert_odds_file, merge_odds_frames
from app.odds.ufc_odds_repository import repository_odds_dataframe

logger = logging.getLogger(__name__)

HOLDOUT_SEASON = 2024
ODDS_2024_CSV = PROJECT_ROOT / "data" / "processed" / "ufc_odds_2024.csv"

# Documented free sources (manual import — no live sportsbook scraping in this repo):
# - BestFightOdds archives (export to CSV)
# - Community UFC odds datasets on Kaggle/GitHub
# - Live-captured ufc_odds_repository snapshots from Run live on board


def load_csv_odds(path: Path | None = None) -> pd.DataFrame:
    """Load normalized odds CSV: date, home_team, away_team, home_ml, away_ml."""
    csv_path = path or ODDS_2024_CSV
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path)
    required = {"date", "home_team", "away_team", "home_ml", "away_ml"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"UFC odds CSV missing columns: {sorted(missing)}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["home_team"] = df["home_team"].map(normalize_fighter_name)
    df["away_team"] = df["away_team"].map(normalize_fighter_name)
    valid = df.apply(
        lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
        axis=1,
    )
    return df[valid].drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)


def import_csv(source: Path, dest: Path | None = None) -> Path:
    """Validate and copy user CSV to the canonical holdout odds path."""
    dest = dest or ODDS_2024_CSV
    df = convert_odds_file(source)
    if df.empty:
        df = load_csv_odds(source)
    if df.empty:
        raise ValueError(f"No valid odds rows in {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = df[list(CANONICAL_COLUMNS)].copy() if all(c in df.columns for c in CANONICAL_COLUMNS) else df
    out.to_csv(dest, index=False)
    logger.info("Wrote %s UFC odds rows to %s", len(out), dest)
    return dest


def import_bulk(sources: list[Path], dest: Path | None = None) -> Path:
    """Merge multiple external odds CSVs into canonical holdout file."""
    dest = dest or ODDS_2024_CSV
    frames: list[pd.DataFrame] = []
    for path in sources:
        if not path.exists():
            logger.warning("Skipping missing odds file: %s", path)
            continue
        try:
            converted = convert_odds_file(path)
            if not converted.empty:
                converted = converted.assign(priority=0)
                frames.append(converted)
        except ValueError as exc:
            logger.warning("Could not convert %s: %s", path, exc)
    existing = load_csv_odds()
    if not existing.empty:
        frames.append(existing.assign(priority=1))
    merged = merge_odds_frames(frames)
    if merged.empty:
        raise ValueError("No valid odds rows from bulk import")
    dest.parent.mkdir(parents=True, exist_ok=True)
    merged[list(CANONICAL_COLUMNS)].to_csv(dest, index=False)
    logger.info("Bulk import wrote %s rows -> %s", len(merged), dest)
    return dest


def load_holdout_odds(holdout_dates: set[str] | None = None) -> pd.DataFrame:
    """
    Priority: free CSV, then live-captured ufc_odds_repository snapshots.

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
