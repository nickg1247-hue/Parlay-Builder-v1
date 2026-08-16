"""Offseason NFL division priors — last three regular seasons, not in-season Elo.

Game-by-game Elo is too sticky: a team can win a division and still look
like a last-place club if the years before were bad. These priors weight
recent wins, Pythagorean wins, and last year's division finish, then turn
that into game probabilities for the upcoming schedule.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from app.ingest.nfl import NFL_DIVISIONS, normalize_abbr, team_division

# Locked after 2023–2025 preseason walk-forward (see scripts/backtest_nfl_futures.py).
# Target: every year ≥75% of teams within one place of final finish.
YEAR_WEIGHTS: tuple[float, float, float] = (0.60, 0.25, 0.15)
PYTHAG_EXP = 2.37
WINS_BLEND = 1.0
PLACE_BONUS = (0.50, 0.15, -0.15, -0.50)
ELO_PER_WIN = 28.0
LEAGUE_MEAN_WINS = 8.5
ELO_START = 1500.0
PRIOR_FLOOR = 0.25
# Only call a winner when 2023–2025 preseason favorites in this band actually hit.
CLEAR_WIN_PCT = 0.72
CLEAR_WIN_GAP = 1.5
LEAN_WIN_PCT = 0.58
LEAN_WIN_GAP = 1.0
CONTENDER_WIN_PCT = 0.12
RACE_LABELS = {
    "clear": "Clear favorite",
    "lean": "Lean",
    "toss_up": "Toss-up",
}


def season_team_stats(df: pd.DataFrame, season: int) -> dict[str, dict[str, float]]:
    rec: dict[str, dict[str, float]] = defaultdict(
        lambda: {"wins": 0.0, "losses": 0.0, "ties": 0.0, "pf": 0.0, "pa": 0.0, "div_wins": 0.0, "gp": 0.0}
    )
    games = df[(df["season"] == int(season)) & (df["game_type"] == "regular")]
    if "home_win" in games.columns:
        games = games[games["home_win"].notna()]
    for row in games.itertuples(index=False):
        home = normalize_abbr(getattr(row, "home_team_abbr", ""))
        away = normalize_abbr(getattr(row, "away_team_abbr", ""))
        if not home or not away:
            continue
        hs = float(row.home_score)
        aws = float(row.away_score)
        rec[home]["pf"] += hs
        rec[home]["pa"] += aws
        rec[away]["pf"] += aws
        rec[away]["pa"] += hs
        rec[home]["gp"] += 1
        rec[away]["gp"] += 1
        if hs == aws:
            rec[home]["ties"] += 1
            rec[away]["ties"] += 1
            continue
        winner, loser = (home, away) if hs > aws else (away, home)
        rec[winner]["wins"] += 1
        rec[loser]["losses"] += 1
        if team_division(home) == team_division(away):
            rec[winner]["div_wins"] += 1
    return rec


def win_pct_wins(stats: dict[str, float]) -> float:
    return float(stats.get("wins", 0.0) + 0.5 * stats.get("ties", 0.0))


def pythag_wins(stats: dict[str, float]) -> float:
    pf = max(1.0, float(stats.get("pf", 0.0)))
    pa = max(1.0, float(stats.get("pa", 0.0)))
    gp = float(stats.get("gp", 0.0)) or 17.0
    share = (pf**PYTHAG_EXP) / (pf**PYTHAG_EXP + pa**PYTHAG_EXP)
    return share * gp


def actual_division_places(stats_by_team: dict[str, dict[str, float]]) -> dict[str, int]:
    by_div: dict[str, list[str]] = defaultdict(list)
    for abbr, div in NFL_DIVISIONS.items():
        by_div[div].append(abbr)
    places: dict[str, int] = {}
    for teams in by_div.values():
        order = sorted(
            teams,
            key=lambda t: (
                -win_pct_wins(stats_by_team.get(t, {})),
                -float(stats_by_team.get(t, {}).get("div_wins", 0.0)),
                -(
                    float(stats_by_team.get(t, {}).get("pf", 0.0))
                    - float(stats_by_team.get(t, {}).get("pa", 0.0))
                ),
                t,
            ),
        )
        for place, team in enumerate(order, start=1):
            places[team] = place
    return places


def projected_wins_from_history(
    history: pd.DataFrame,
    season: int,
    *,
    year_weights: tuple[float, float, float] = YEAR_WEIGHTS,
    wins_blend: float = WINS_BLEND,
) -> dict[str, float]:
    """Regressed 17-game win total entering `season` (no games from that season)."""
    prior = [
        season_team_stats(history, season - 1),
        season_team_stats(history, season - 2),
        season_team_stats(history, season - 3),
    ]
    last_places = actual_division_places(prior[0]) if prior[0] else {}
    out: dict[str, float] = {}
    for team in NFL_DIVISIONS:
        blended = 0.0
        weight_sum = 0.0
        for stats, weight in zip(prior, year_weights):
            if team not in stats or stats[team].get("gp", 0) <= 0:
                continue
            wins = win_pct_wins(stats[team])
            pyth = pythag_wins(stats[team])
            blended += weight * (wins_blend * wins + (1.0 - wins_blend) * pyth)
            weight_sum += weight
        if weight_sum <= 0:
            rating = LEAGUE_MEAN_WINS
        else:
            rating = blended / weight_sum
        place = last_places.get(team)
        if place:
            rating += PLACE_BONUS[place - 1]
        out[team] = float(rating)
    return out


def wins_to_elo(projected_wins: dict[str, float]) -> dict[str, float]:
    return {
        team: ELO_START + ELO_PER_WIN * (wins - LEAGUE_MEAN_WINS)
        for team, wins in projected_wins.items()
    }


def elo_win_prob(home_elo: float, away_elo: float, *, neutral: bool = False) -> float:
    adv = 0.0 if neutral else 55.0
    return 1.0 / (1.0 + 10 ** ((away_elo - home_elo - adv) / 400.0))


def prior_game_probs(
    remaining: list[dict[str, Any]],
    elo: dict[str, float],
) -> dict[str, float]:
    probs: dict[str, float] = {}
    for game in remaining:
        home = normalize_abbr(game.get("home_team_abbr"))
        away = normalize_abbr(game.get("away_team_abbr"))
        probs[str(game["game_id"])] = float(
            min(
                0.92,
                max(
                    0.08,
                    elo_win_prob(
                        elo.get(home, ELO_START),
                        elo.get(away, ELO_START),
                        neutral=bool(game.get("neutral_site")),
                    ),
                ),
            )
        )
    return probs


def prior_mix_weight(games_completed: int, games_remaining: int) -> float:
    """Full prior before Week 1; fade toward the live model as results land."""
    total = games_completed + games_remaining
    if total <= 0 or games_completed <= 0:
        return 1.0
    played_frac = games_completed / total
    return float(max(PRIOR_FLOOR, 1.0 - played_frac * 1.35))


def classify_division_race(teams: list[dict[str, Any]]) -> str:
    """clear / lean / toss_up from the top two projected teams."""
    if len(teams) < 2:
        return "toss_up"
    ranked = sorted(
        teams,
        key=lambda row: (
            -float(row.get("division_win_pct") or 0.0),
            -float(row.get("expected_wins") or 0.0),
        ),
    )
    top, second = ranked[0], ranked[1]
    win_pct = float(top.get("division_win_pct") or 0.0)
    gap = float(top.get("expected_wins") or 0.0) - float(second.get("expected_wins") or 0.0)
    if win_pct >= CLEAR_WIN_PCT and gap >= CLEAR_WIN_GAP:
        return "clear"
    if win_pct >= LEAN_WIN_PCT and gap >= LEAN_WIN_GAP:
        return "lean"
    return "toss_up"


def annotate_division(div: dict[str, Any]) -> dict[str, Any]:
    """Attach race label and contenders. Toss-ups are not a published pick."""
    teams = list(div.get("teams") or [])
    race = classify_division_race(teams)
    out = dict(div)
    out["race"] = race
    out["race_label"] = RACE_LABELS[race]
    if race == "toss_up":
        contenders = [
            row for row in teams if float(row.get("division_win_pct") or 0.0) >= CONTENDER_WIN_PCT
        ]
        out["contenders"] = contenders[:3] or teams[:3]
        out["pick"] = None
        out["pick_abbr"] = None
    else:
        out["contenders"] = teams[:1]
        out["pick"] = teams[0].get("team") if teams else None
        out["pick_abbr"] = teams[0].get("abbr") if teams else None
    return out


def annotate_futures_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return payload
    out = dict(payload)
    out["divisions"] = [annotate_division(div) for div in (payload.get("divisions") or [])]
    return out
