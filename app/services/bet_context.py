"""Shared bet context: team form windows and line-strength heuristics."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.mlb_baseline import load_games
from app.odds.team_aliases import normalize_team_name


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def team_win_rate_windows(
    team: str,
    before_date: date,
    *,
    games: pd.DataFrame | None = None,
) -> dict[str, float | None]:
    """Win rates for picked team strictly before *before_date* (L5, L10, season year)."""
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

    season_played = played[played["date"].dt.year == before_date.year]

    def _rate(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        wins = 0
        for row in frame.itertuples(index=False):
            home = normalize_team_name(str(row.home_team))
            if home == norm:
                wins += 1 if bool(row.home_win) else 0
            else:
                wins += 0 if bool(row.home_win) else 1
        return round(wins / len(frame), 4)

    return {
        "win_rate_l5": _rate(played.head(5)),
        "win_rate_l10": _rate(played.head(10)),
        "win_rate_season": _rate(season_played),
    }


def _line_strength_payload(level: str, insight: str) -> dict[str, str]:
    labels = {
        "strong": "Strong line",
        "moderate": "Moderate line",
        "weak": "Weak line",
    }
    return {
        "line_strength": level,
        "line_strength_label": labels.get(level, level.title()),
        "line_insight": insight,
    }


def ml_bet_line_strength(
    pick: dict[str, Any],
    game: dict[str, Any] | None,
    win_rates: dict[str, float | None],
) -> dict[str, str]:
    """Heuristic ML line quality from model edge, team form, and opposing starter ERA."""
    edge = float(pick.get("edge") or 0)
    side = pick.get("side")
    l5 = win_rates.get("win_rate_l5")
    l10 = win_rates.get("win_rate_l10")
    season = win_rates.get("win_rate_season")

    score = 0
    notes: list[str] = []

    if edge >= 0.08:
        score += 2
        notes.append(f"Model edge {edge:.1%}")
    elif edge >= 0.05:
        score += 1
        notes.append(f"Model edge {edge:.1%}")
    elif edge > 0:
        notes.append(f"Model edge {edge:.1%}")

    for label, rate in (("L5", l5), ("L10", l10), ("Season", season)):
        if rate is None:
            continue
        if rate >= 0.6:
            score += 1 if label == "L10" else 0
            notes.append(f"Team {label} {rate:.0%} wins")
        elif rate <= 0.4 and label == "L10":
            score -= 1
            notes.append(f"Team cold {label} {rate:.0%}")

    opp_era: float | None = None
    if game and side in ("home", "away"):
        opp_key = "away_pitcher_era" if side == "home" else "home_pitcher_era"
        opp_era = _safe_float(game.get(opp_key))
        opp_name = (
            game.get("away_starting_pitcher")
            if side == "home"
            else game.get("home_starting_pitcher")
        )
        if opp_era is not None:
            if opp_era >= 4.5:
                score += 1
                who = f" vs {opp_name}" if opp_name else ""
                notes.append(f"Opposing starter ERA {opp_era:.2f}{who} (soft)")
            elif opp_era <= 3.2:
                score -= 1
                who = f" vs {opp_name}" if opp_name else ""
                notes.append(f"Opposing starter ERA {opp_era:.2f}{who} (tough)")

    if score >= 3:
        level = "strong"
    elif score >= 1:
        level = "moderate"
    else:
        level = "weak"

    insight = " · ".join(notes[:4]) if notes else "Limited matchup context"
    era_notes = [n for n in notes if "ERA" in n]
    if era_notes:
        rest = [n for n in notes if "ERA" not in n]
        insight = " · ".join((era_notes + rest)[:4])
    return _line_strength_payload(level, insight)


def enrich_ml_single_pick(
    pick: dict[str, Any],
    game: dict[str, Any] | None,
    game_date: date,
    *,
    games: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Attach team form windows and ML line strength to a +EV single."""
    team = str(pick.get("team") or "")
    rates = team_win_rate_windows(team, game_date, games=games)
    strength = ml_bet_line_strength(pick, game, rates)
    out = {**pick, **rates, **strength}
    if game:
        out["game_id"] = game.get("game_id")
        out["home_team"] = game.get("home_team")
        out["away_team"] = game.get("away_team")
    return out


def enrich_ml_singles(
    picks: list[dict[str, Any]],
    slate: list[dict[str, Any]],
    game_date: date,
) -> list[dict[str, Any]]:
    by_matchup = {g.get("matchup"): g for g in slate}
    by_id = {str(g.get("game_id", "")): g for g in slate if g.get("game_id")}
    games_df = load_games()
    enriched: list[dict[str, Any]] = []
    for pick in picks:
        game = by_id.get(str(pick.get("game_id") or "")) or by_matchup.get(pick.get("matchup"))
        enriched.append(
            enrich_ml_single_pick(pick, game, game_date, games=games_df)
        )
    return enriched


def form_composite_score(pick: dict[str, Any]) -> float:
    """Average of available L5 / L10 / season win rates (higher = hotter team form)."""
    rates = [
        pick.get("win_rate_l5"),
        pick.get("win_rate_l10"),
        pick.get("win_rate_season"),
    ]
    vals = [float(r) for r in rates if r is not None]
    if not vals:
        return -1.0
    return sum(vals) / len(vals)
