"""Load free historical MLB moneylines from community SBR dataset release."""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

logger = logging.getLogger(__name__)

# Pre-built dataset (SportsBookReview-derived); we do not scrape sportsbooks.
ODDS_DATASET_URL = (
    "https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/download/"
    "dataset/mlb_odds_dataset.json"
)
RAW_JSON_CACHE = PROJECT_ROOT / "data" / "processed" / "mlb_odds_dataset.json"
ODDS_2025_CSV = PROJECT_ROOT / "data" / "processed" / "mlb_odds_2025.csv"
TOTALS_2025_CSV = PROJECT_ROOT / "data" / "processed" / "mlb_totals_2025.csv"
TOTALS_2024_CSV = PROJECT_ROOT / "data" / "processed" / "mlb_totals_2024.csv"
TARGET_SEASON = 2025
LAB_TOTALS_SEASONS = (2024, 2025)


def totals_odds_csv_path(season: int) -> Path:
    return PROJECT_ROOT / "data" / "processed" / f"mlb_totals_{season}.csv"


def download_raw_dataset(force: bool = False) -> Path:
    RAW_JSON_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if RAW_JSON_CACHE.exists() and not force:
        logger.info("Using cached dataset: %s", RAW_JSON_CACHE)
        return RAW_JSON_CACHE

    logger.info("Downloading MLB odds dataset (~76 MB)...")
    with httpx.Client(timeout=600.0, follow_redirects=True) as client:
        with client.stream("GET", ODDS_DATASET_URL) as response:
            response.raise_for_status()
            with RAW_JSON_CACHE.open("wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
    logger.info("Saved %s", RAW_JSON_CACHE)
    return RAW_JSON_CACHE


def _consensus_totals(
    totals_entries: list[dict[str, Any]],
) -> tuple[float | None, int | None, int | None]:
    lines: list[float] = []
    over_odds: list[int] = []
    under_odds: list[int] = []
    for entry in totals_entries:
        line = entry.get("currentLine") or entry.get("openingLine")
        if not line:
            continue
        total = line.get("total")
        over = line.get("overOdds")
        under = line.get("underOdds")
        if total is not None and over is not None and under is not None:
            lines.append(float(total))
            over_odds.append(int(over))
            under_odds.append(int(under))
    if not lines:
        return None, None, None
    return (
        float(statistics.median(lines)),
        int(statistics.median(over_odds)),
        int(statistics.median(under_odds)),
    )


def _consensus_moneyline(moneyline_entries: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    home_odds: list[int] = []
    away_odds: list[int] = []
    for entry in moneyline_entries:
        line = entry.get("currentLine") or entry.get("openingLine")
        if not line:
            continue
        home = line.get("homeOdds")
        away = line.get("awayOdds")
        if home is not None and away is not None:
            home_odds.append(int(home))
            away_odds.append(int(away))
    if not home_odds:
        return None, None
    return int(statistics.median(home_odds)), int(statistics.median(away_odds))


def parse_2025_odds(raw_path: Path) -> pd.DataFrame:
    with raw_path.open(encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict[str, Any]] = []
    for date_str, games in data.items():
        if not date_str.startswith(str(TARGET_SEASON)):
            continue
        for game in games:
            view = game.get("gameView", {})
            home = view.get("homeTeam", {})
            away = view.get("awayTeam", {})
            home_name = normalize_team_name(home.get("fullName", ""))
            away_name = normalize_team_name(away.get("fullName", ""))
            home_ml, away_ml = _consensus_moneyline(game.get("odds", {}).get("moneyline", []))
            if home_ml is None or not is_valid_american_odds(home_ml):
                continue
            if not is_valid_american_odds(away_ml):
                continue
            rows.append(
                {
                    "date": date_str,
                    "home_team": home_name,
                    "away_team": away_name,
                    "home_ml": home_ml,
                    "away_ml": away_ml,
                    "odds_line_type": "currentLine_median",
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["date", "home_team", "away_team"], keep="first")
    return df.sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)


def parse_season_totals(raw_path: Path, season: int) -> pd.DataFrame:
    """Parse O/U lines from the free SBR JSON release for a given season."""
    with raw_path.open(encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict[str, Any]] = []
    prefix = str(season)
    for date_str, games in data.items():
        if not date_str.startswith(prefix):
            continue
        for game in games:
            view = game.get("gameView", {})
            home_name = normalize_team_name(view.get("homeTeam", {}).get("fullName", ""))
            away_name = normalize_team_name(view.get("awayTeam", {}).get("fullName", ""))
            ou_line, over_odds, under_odds = _consensus_totals(
                game.get("odds", {}).get("totals", [])
            )
            if ou_line is None:
                continue
            if not is_valid_american_odds(over_odds) or not is_valid_american_odds(
                under_odds
            ):
                continue
            rows.append(
                {
                    "date": date_str,
                    "home_team": home_name,
                    "away_team": away_name,
                    "ou_line": ou_line,
                    "over_odds": over_odds,
                    "under_odds": under_odds,
                    "odds_line_type": "currentLine_median",
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).sort_values(["date", "home_team", "away_team"]).reset_index(drop=True)


def parse_2025_totals(raw_path: Path) -> pd.DataFrame:
    return parse_season_totals(raw_path, TARGET_SEASON)


def load_or_build_season_totals_csv(
    season: int,
    *,
    force_download: bool = False,
    force_parse: bool = False,
) -> pd.DataFrame:
    if season not in LAB_TOTALS_SEASONS:
        raise ValueError(f"Lab totals odds supported for seasons {LAB_TOTALS_SEASONS}")

    out_path = totals_odds_csv_path(season)
    if out_path.exists() and not force_download and not force_parse:
        return pd.read_csv(out_path)

    raw_path = download_raw_dataset(force=force_download)
    df = parse_season_totals(raw_path, season)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("Wrote %s totals rows to %s", len(df), out_path)
    return df


def load_totals_odds_for_season(season: int) -> pd.DataFrame | None:
    path = totals_odds_csv_path(season)
    if not path.exists():
        return None
    return pd.read_csv(path)


def load_or_build_2025_csv(
    force_download: bool = False, force_parse: bool = False
) -> pd.DataFrame:
    if ODDS_2025_CSV.exists() and not force_download and not force_parse:
        return pd.read_csv(ODDS_2025_CSV)

    raw_path = download_raw_dataset(force=force_download)
    df = parse_2025_odds(raw_path)
    ODDS_2025_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ODDS_2025_CSV, index=False)
    logger.info("Wrote %s rows to %s", len(df), ODDS_2025_CSV)
    return df


def load_or_build_2025_totals_csv(
    force_download: bool = False, force_parse: bool = False
) -> pd.DataFrame:
    return load_or_build_season_totals_csv(
        TARGET_SEASON,
        force_download=force_download,
        force_parse=force_parse,
    )
