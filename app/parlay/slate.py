"""Build today's MLB slate with model features from game history."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import httpx
import pandas as pd

from app.models.mlb_baseline import (
    NEUTRAL_LAST10_RUN_DIFF,
    NEUTRAL_LAST10_WIN_PCT,
    load_games,
    load_model_artifact,
    predict_home_win_proba,
)
from app.odds.team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def _last_n_metrics(
    history: list[tuple[int, int]], n: int = 10
) -> tuple[float | None, float | None]:
    if not history:
        return None, None
    window = history[-n:]
    wins = [w for w, _ in window]
    diffs = [d for _, d in window]
    return sum(wins) / len(window), sum(diffs) / len(diffs)


def _build_team_state(history: pd.DataFrame) -> tuple[
    dict[str, list[tuple[int, int]]], dict[str, datetime]
]:
    team_history: dict[str, list[tuple[int, int]]] = defaultdict(list)
    team_last_date: dict[str, datetime] = {}
    for row in history.itertuples(index=False):
        game_date = pd.to_datetime(row.date)
        if getattr(game_date, "tzinfo", None) is not None:
            game_date = game_date.tz_convert(None)
        home_win = 1 if row.home_score > row.away_score else 0
        away_win = 1 - home_win
        home_rd = row.home_score - row.away_score
        away_rd = row.away_score - row.home_score
        team_history[row.home_team].append((home_win, home_rd))
        team_history[row.away_team].append((away_win, away_rd))
        team_last_date[row.home_team] = game_date
        team_last_date[row.away_team] = game_date
    return team_history, team_last_date


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
    team_history, team_last_date = _build_team_state(history)
    artifact = load_model_artifact()
    era_medians = artifact["era_medians"]
    rest_fill = artifact["rest_fill"]

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

        h_wp, h_rd = _last_n_metrics(team_history[home_team])
        a_wp, a_rd = _last_n_metrics(team_history[away_team])
        home_rest = (
            (gdt - team_last_date[home_team]).days if home_team in team_last_date else None
        )
        away_rest = (
            (gdt - team_last_date[away_team]).days if away_team in team_last_date else None
        )

        season = gdt.year
        home_era = era_medians.get(season, era_medians["default"])
        away_era = era_medians.get(season, era_medians["default"])

        rows.append(
            {
                "game_id": str(game["gamePk"]),
                "date": game_date.isoformat(),
                "home_team": home_team,
                "away_team": away_team,
                "home_pitcher_era": home_era,
                "away_pitcher_era": away_era,
                "home_last10_win_pct": h_wp if h_wp is not None else NEUTRAL_LAST10_WIN_PCT,
                "away_last10_win_pct": a_wp if a_wp is not None else NEUTRAL_LAST10_WIN_PCT,
                "home_last10_run_diff": h_rd if h_rd is not None else NEUTRAL_LAST10_RUN_DIFF,
                "away_last10_run_diff": a_rd if a_rd is not None else NEUTRAL_LAST10_RUN_DIFF,
                "home_rest_days": home_rest if home_rest is not None else rest_fill,
                "away_rest_days": away_rest if away_rest is not None else rest_fill,
                "home_starting_pitcher": _pitcher_name(home),
                "away_starting_pitcher": _pitcher_name(away),
                "season": season,
            }
        )

    if not rows:
        return pd.DataFrame()
    slate = pd.DataFrame(rows)
    slate["model_prob_home"] = predict_home_win_proba(slate)
    slate["model_prob_away"] = 1.0 - slate["model_prob_home"]
    return slate


def build_slate_from_history(game_date: date) -> pd.DataFrame:
    """Replay slate from ingested games (for historical --use-cache demos)."""
    df = load_games()
    day = df[df["date"].dt.date == game_date].copy()
    if day.empty:
        return pd.DataFrame()
    day["date"] = game_date.isoformat()
    day["model_prob_home"] = predict_home_win_proba(day)
    day["model_prob_away"] = 1.0 - day["model_prob_home"]
    return day
