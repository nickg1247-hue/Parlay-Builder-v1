"""Build today's MLB slate with model features from game history."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
import pandas as pd

from app.features.mlb_pregame import build_features_for_slate
from app.models.mlb_baseline import load_games, predict_home_win_proba
from app.odds.team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def fetch_mlb_schedule_day(game_date: date) -> list[dict[str, Any]]:
    params = {
        "sportId": 1,
        "date": game_date.isoformat(),
        "hydrate": "probablePitcher",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.get(MLB_SCHEDULE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    games: list[dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def _pitcher_name(team_blob: dict[str, Any]) -> str | None:
    pitcher = team_blob.get("probablePitcher") or {}
    return pitcher.get("fullName")


def build_slate_dataframe(
    game_date: date,
    history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    history = history if history is not None else load_games()
    history = history[history["date"] < pd.Timestamp(game_date)].copy()

    api_games = fetch_mlb_schedule_day(game_date)
    rows: list[dict[str, Any]] = []
    for game in api_games:
        status = game.get("status", {}).get("abstractGameState", "")
        if status in ("Final", "Game Over"):
            continue
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        home_team = normalize_team_name(home["team"]["name"])
        away_team = normalize_team_name(away["team"]["name"])
        gdt = pd.to_datetime(game.get("gameDate", game_date.isoformat()), utc=True)
        gdt = gdt.tz_convert(None) if gdt.tzinfo else gdt
        season = gdt.year

        rows.append(
            {
                "game_id": str(game["gamePk"]),
                "date": game_date.isoformat(),
                "home_team": home_team,
                "away_team": away_team,
                "home_starting_pitcher": _pitcher_name(home),
                "away_starting_pitcher": _pitcher_name(away),
                "season": season,
            }
        )

    if not rows:
        return pd.DataFrame()

    slate = build_features_for_slate(pd.DataFrame(rows), history_df=history)
    slate["model_prob_home"] = predict_home_win_proba(slate)
    slate["model_prob_away"] = 1.0 - slate["model_prob_home"]
    return slate


def build_slate_from_history(game_date: date) -> pd.DataFrame:
    """Replay slate from ingested games (for historical --use-cache demos)."""
    df = load_games()
    day = df[df["date"].dt.date == game_date].copy()
    if day.empty:
        return pd.DataFrame()
    hist = df[df["date"] < pd.Timestamp(game_date)].copy()
    day["date"] = game_date.isoformat()
    featured = build_features_for_slate(day, history_df=hist)
    featured["model_prob_home"] = predict_home_win_proba(featured)
    featured["model_prob_away"] = 1.0 - featured["model_prob_home"]
    return featured
