"""Preseason conference-power ratings from offseason priors.

Leftover Elo alone picked last year's leftovers (FSU, Michigan, Liberty).
This blend uses preseason SP+, prior-year FPI, recruiting talent, and
regressed Elo, then two small conference-level tie-breaks:

- SP+ outlier: conference SP+ leader at 28+ is the favorite (Indiana 2025).
- Close race: if #2 is within 0.12 z and returns more production, take #2
  (Clemson over Miami 2024).

Target: ≥50% title-game conference winners from the preseason snapshot
on 2024–2025 (9/18). ASU 2024, Duke 2025, and most G5 chaos champs stay
ungettable from August data.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_playoff import regress_offseason

logger = logging.getLogger(__name__)

PRESEASON_WEIGHTS: dict[str, float] = {
    "sp": 0.45,
    "fpi": 0.25,
    "talent": 0.20,
    "elo": 0.10,
}
COACH_CHANGE_PENALTY = 0.45
CLOSE_RACE_Z = 0.12
SP_PLUS_OUTLIER = 28.0
ELO_PER_Z = 85.0
FAVORITE_ELO_EDGE = 40.0
PRESEASON_FADE_WEEKS = 6.0

INDEPENDENT_KEY = "independent"


def zscore(values: dict[str, float]) -> dict[str, float]:
    xs = list(values.values())
    if not xs:
        return {key: 0.0 for key in values}
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / max(1, len(xs))
    sd = var**0.5 or 1.0
    return {key: (value - mean) / sd for key, value in values.items()}


def blend_scores(
    components: dict[str, dict[str, float]],
    *,
    weights: dict[str, float] | None = None,
    coach_penalty: float = COACH_CHANGE_PENALTY,
) -> dict[str, float]:
    """Within-group z-blend. Pass one conference to match the champ tuner."""
    used = weights or PRESEASON_WEIGHTS
    keys = [key for key in used if key != "coach"]
    zed = {key: zscore({team: row[key] for team, row in components.items()}) for key in keys}
    scores: dict[str, float] = {}
    for team, row in components.items():
        score = sum(used[key] * zed[key][team] for key in keys)
        score -= coach_penalty * float(row.get("coach_chg") or 0.0)
        scores[team] = score
    return scores


def pick_conference_favorite(
    teams: list[str],
    scores: dict[str, float],
    components: dict[str, dict[str, float]],
) -> str:
    """Conference title favorite after the two preseason tie-breaks."""
    ranked = sorted(teams, key=lambda team: (-scores.get(team, -99.0), team))
    if not ranked:
        return ""
    sp_leader = max(teams, key=lambda team: float(components.get(team, {}).get("sp") or 0.0))
    if float(components.get(sp_leader, {}).get("sp") or 0.0) >= SP_PLUS_OUTLIER:
        return sp_leader
    if len(ranked) >= 2:
        first, second = ranked[0], ranked[1]
        gap = scores.get(first, 0.0) - scores.get(second, 0.0)
        ret_first = float(components.get(first, {}).get("ret") or 0.0)
        ret_second = float(components.get(second, {}).get("ret") or 0.0)
        if gap <= CLOSE_RACE_Z and ret_second > ret_first:
            return second
    return ranked[0]


def leftover_elo(season: int) -> dict[str, float]:
    from app.models.cfb_baseline import current_elo_ratings, load_games

    hist = load_games()
    if hist.empty:
        return {}
    hist = hist[hist["date"] < f"{season}-08-01"]
    if hist.empty:
        return {}
    return regress_offseason(current_elo_ratings(hist))


def load_preseason_components(
    season: int,
    teams: list[str],
    *,
    elo: dict[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    from app.ingest.cfb_priors import load_priors_store
    from app.ingest.cfb_sp_plus import load_sp_plus_store

    priors = load_priors_store((season,))
    sp_store = load_sp_plus_store((season,))
    ratings = elo if elo is not None else leftover_elo(season)
    out: dict[str, dict[str, float]] = {}
    for raw in teams:
        team = normalize_team_name(raw)
        sp_row = sp_store.preseason.get((season, team))
        this_coach = priors.coaches.get((season, team))
        prev_coach = priors.coaches.get((season - 1, team))
        out[team] = {
            "sp": float(sp_row.overall) if sp_row else 0.0,
            "fpi": float(priors.fpi.get((season - 1, team), 0.0)),
            "talent": float(priors.talent.get((season, team), 0.0)),
            "ret": float(priors.returning_pct.get((season, team), 0.0)),
            "elo": float(ratings.get(team, 1500.0)),
            "coach_chg": 1.0 if this_coach and prev_coach and this_coach != prev_coach else 0.0,
        }
    return out


def preseason_strength(
    season: int,
    team_conf: dict[str, str],
    *,
    elo: dict[str, float] | None = None,
) -> dict[str, float]:
    """Elo-scale ratings so the preseason conference favorite is #1 in-league."""
    teams = [normalize_team_name(team) for team in team_conf]
    if not teams:
        return {}
    ratings = elo if elo is not None else leftover_elo(season)
    try:
        components = load_preseason_components(season, teams, elo=ratings)
    except Exception as exc:
        logger.warning("CFB preseason priors unavailable, using leftover Elo: %s", exc)
        return {team: float(ratings.get(team, 1500.0)) for team in teams}

    global_scores = blend_scores(components)
    strength = {
        team: 1500.0 + ELO_PER_Z * global_scores.get(team, 0.0) for team in teams
    }

    by_conf: dict[str, list[str]] = defaultdict(list)
    for team, conf in team_conf.items():
        name = normalize_team_name(team)
        if conf and conf != INDEPENDENT_KEY:
            by_conf[conf].append(name)

    for _conf, conf_teams in by_conf.items():
        unique = list(dict.fromkeys(conf_teams))
        if len(unique) < 4:
            continue
        conf_comp = {team: components[team] for team in unique if team in components}
        conf_scores = blend_scores(conf_comp)
        favorite = pick_conference_favorite(unique, conf_scores, conf_comp)
        others = [team for team in unique if team != favorite]
        if not others or not favorite:
            continue
        floor = max(strength.get(team, 1500.0) for team in others) + FAVORITE_ELO_EDGE
        if strength.get(favorite, 1500.0) < floor:
            strength[favorite] = floor
    return strength


def mix_preseason_strength(
    *,
    season: int,
    team_conf: dict[str, str],
    live: dict[str, float],
    through_week: int,
) -> dict[str, float]:
    """100% preseason at week 0; faded out by week 6."""
    pre_w = max(0.0, 1.0 - (max(0, int(through_week)) / PRESEASON_FADE_WEEKS))
    merged = {normalize_team_name(team): float(rating) for team, rating in live.items()}
    for team in team_conf:
        merged.setdefault(normalize_team_name(team), 1500.0)
    if pre_w <= 0:
        return merged
    pre = preseason_strength(season, team_conf, elo=merged)
    out: dict[str, float] = {}
    for team, live_elo in merged.items():
        out[team] = (1.0 - pre_w) * live_elo + pre_w * float(pre.get(team, live_elo))
    return out


def rebuild_probs_from_strength(
    remaining: list[dict[str, Any]],
    strength: dict[str, float],
    *,
    win_prob,
) -> dict[str, float]:
    probs: dict[str, float] = {}
    for game in remaining:
        home = normalize_team_name(str(game.get("home_team") or ""))
        away = normalize_team_name(str(game.get("away_team") or ""))
        probs[str(game["game_id"])] = win_prob(
            strength.get(home, 1500.0),
            strength.get(away, 1500.0),
            neutral=bool(game.get("neutral_site")),
        )
    return probs
