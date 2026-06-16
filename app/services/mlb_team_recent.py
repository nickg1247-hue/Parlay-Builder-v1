"""Recent completed MLB games per team (for game-page form blocks)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.mlb_baseline import load_games
from app.odds.team_aliases import normalize_team_name


def _short_team(name: str) -> str:
    parts = name.split()
    return parts[-1] if len(parts) >= 2 else name


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _game_entry(row: pd.Series, team: str) -> dict[str, Any] | None:
    home = str(row["home_team"])
    away = str(row["away_team"])
    norm = normalize_team_name(team)
    if normalize_team_name(home) == norm:
        team_runs = int(row["home_score"])
        opp_runs = int(row["away_score"])
        opponent = away
        won = bool(row["home_win"])
        is_home = True
    elif normalize_team_name(away) == norm:
        team_runs = int(row["away_score"])
        opp_runs = int(row["home_score"])
        opponent = home
        won = not bool(row["home_win"])
        is_home = False
    else:
        return None

    return {
        "date": _as_date(row["date"]).isoformat(),
        "opponent": opponent,
        "opponent_short": _short_team(opponent),
        "is_home": is_home,
        "at_vs": "vs" if is_home else "@",
        "won": won,
        "team_runs": team_runs,
        "opp_runs": opp_runs,
        "score": f"{team_runs}-{opp_runs}",
        "result": f"{'W' if won else 'L'} {team_runs}-{opp_runs}",
    }


def team_last_n_games(
    team: str,
    before_date: date,
    n: int = 5,
    games: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Last *n* completed games for *team* strictly before *before_date*, newest first."""
    if games is None:
        games = load_games()

    norm = normalize_team_name(team)
    completed = games[
        (games["date"].dt.date < before_date)
        & games["home_score"].notna()
        & games["away_score"].notna()
        & games["home_win"].notna()
    ]
    played = completed[
        completed["home_team"].map(normalize_team_name).eq(norm)
        | completed["away_team"].map(normalize_team_name).eq(norm)
    ].sort_values("date", ascending=False)

    out: list[dict[str, Any]] = []
    for _, row in played.head(n).iterrows():
        entry = _game_entry(row, team)
        if entry:
            out.append(entry)
    return out


def recent_games_for_matchup(
    home_team: str,
    away_team: str,
    game_date: date,
    n: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    games = load_games()
    return {
        "home": team_last_n_games(home_team, game_date, n=n, games=games),
        "away": team_last_n_games(away_team, game_date, n=n, games=games),
    }
