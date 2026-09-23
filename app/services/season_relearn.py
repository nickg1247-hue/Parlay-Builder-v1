"""In-season moneyline relearn for NFL and college football.

The saved model stays in place. This fits how the current power rankings
have lined up with this season's winners, then blends that into upcoming
picks. Clicking relearn again rebuilds it as the season changes.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sklearn.linear_model import LogisticRegression

from app.config import PROJECT_ROOT
from app.services.power_rankings import football_season

logger = logging.getLogger(__name__)

MIN_FIT_GAMES = 12
PRIORS = {
    "nfl": {"intercept": 0.0, "power_coef": 0.12, "home_coef": 0.28},
    "cfb": {"intercept": 0.0, "power_coef": 0.08, "home_coef": 0.22},
}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def artifact_path(sport: str) -> Path:
    name = "nfl_season_relearn.json" if sport == "nfl" else "cfb_season_relearn.json"
    return PROJECT_ROOT / "data" / "processed" / name


def blend_weight_for_games(average_games: float) -> float:
    """Small early, larger once teams have a real body of results. Caps at 62%."""
    games = max(0.0, float(average_games))
    raw = 0.22 + 0.40 * (1.0 - math.exp(-games / 5.0))
    return round(min(0.62, max(0.22, raw)), 3)


def fit_power_win_model(sport: str, samples: list[tuple[float, float, int]]) -> dict[str, Any]:
    """Map (power gap, home field) to a home-win probability.

    samples are (home_power - away_power, 1 if home field else 0, home_win).
    A short sample, or a fit that ranks the better team as the underdog, keeps
    the sport's prior scale.
    """
    prior = dict(PRIORS[sport])
    prior["games_fit"] = len(samples)
    prior["fit_source"] = "prior"
    if len(samples) < MIN_FIT_GAMES:
        return prior
    labels = {row[2] for row in samples}
    if len(labels) < 2:
        return prior
    features = [[row[0], row[1]] for row in samples]
    target = [row[2] for row in samples]
    model = LogisticRegression(C=1.0, max_iter=400)
    model.fit(features, target)
    power_coef = float(model.coef_[0][0])
    if power_coef <= 0:
        return prior
    # A short slate can invent a huge home/away bias. Keep most of the prior
    # until this season has a real body of results.
    trust = min(0.85, max(0.0, (len(samples) - MIN_FIT_GAMES) / 80.0))
    return {
        "intercept": trust * float(model.intercept_[0]) + (1.0 - trust) * prior["intercept"],
        "power_coef": trust * power_coef + (1.0 - trust) * prior["power_coef"],
        "home_coef": trust * float(model.coef_[0][1]) + (1.0 - trust) * prior["home_coef"],
        "games_fit": len(samples),
        "fit_source": "current_season",
    }


def power_win_probability(
    intercept: float,
    power_coef: float,
    home_coef: float,
    power_diff: float,
    *,
    neutral: bool,
) -> float:
    home_field = 0.0 if neutral else 1.0
    score = intercept + power_coef * power_diff + home_coef * home_field
    score = max(-8.0, min(8.0, score))
    probability = 1.0 / (1.0 + math.exp(-score))
    return min(0.97, max(0.03, probability))


def rankings_blend_weight(base_weight: float, power_probability: float) -> float:
    """Let a clear power gap decide the pick instead of the preseason model."""
    gap = abs(float(power_probability) - 0.5)
    return round(min(0.92, max(float(base_weight), 0.45) + 2.2 * gap), 3)


def mix_probability(base: float, power_probability: float, weight: float) -> float:
    mixed = (1.0 - weight) * float(base) + weight * float(power_probability)
    return min(0.99, max(0.01, mixed))


def public_summary(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if not artifact:
        return {"applied": False}
    return {key: value for key, value in artifact.items() if key != "ratings"}


def load_relearn(sport: str) -> dict[str, Any] | None:
    path = artifact_path(sport)
    if not path.exists():
        _CACHE.pop(sport, None)
        return None
    mtime = path.stat().st_mtime
    cached = _CACHE.get(sport)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or not payload.get("applied"):
        return None
    _CACHE[sport] = (mtime, payload)
    return payload


def relearn_status(sport: str) -> dict[str, Any]:
    summary = public_summary(load_relearn(sport))
    summary["sport"] = sport
    return summary


def _save(artifact: dict[str, Any]) -> None:
    path = artifact_path(artifact["sport"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    _CACHE.pop(artifact["sport"], None)


def _team_key(sport: str, name: str) -> str:
    if sport == "nfl":
        from app.ingest.nfl import normalize_abbr

        return normalize_abbr(name)
    from app.odds.cfb_team_aliases import normalize_team_name

    return normalize_team_name(name)


def _ratings_from_payload(sport: str, payload: dict[str, Any]) -> dict[str, float]:
    ratings: dict[str, float] = {}
    for team in payload.get("teams") or []:
        if sport == "nfl":
            key = _team_key(sport, str(team.get("abbr") or ""))
        else:
            key = _team_key(sport, str(team.get("team") or ""))
        power = team.get("power")
        if key and power is not None:
            ratings[key] = float(power)
    return ratings


def _lookup_rating(sport: str, ratings: dict[str, float], name: str) -> float | None:
    key = _team_key(sport, name)
    if not key:
        return None
    if key in ratings:
        return ratings[key]
    folded = key.casefold()
    for candidate, power in ratings.items():
        if candidate.casefold() == folded:
            return power
    return None


def _completed_games(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame.iloc[0:0]
    dated = frame.copy()
    dated["date"] = pd.to_datetime(dated["date"])
    season = football_season(as_of)
    mask = dated["date"].dt.date < as_of
    if "season" in dated.columns:
        mask = mask & (dated["season"] == season)
    if "game_type" in dated.columns:
        mask = mask & dated["game_type"].fillna("regular").ne("preseason")
    completed = dated.loc[mask].copy()
    if "home_score" not in completed.columns or "away_score" not in completed.columns:
        return completed.iloc[0:0]
    completed = completed[completed["home_score"].notna() & completed["away_score"].notna()]
    completed = completed[completed["home_score"] != completed["away_score"]]
    if "game_id" in completed.columns:
        completed = completed.drop_duplicates("game_id", keep="last")
    return completed


def _week_bucket(frame: pd.DataFrame) -> pd.Series:
    if "week" in frame.columns:
        week = pd.to_numeric(frame["week"], errors="coerce").fillna(0).astype(int)
        if len(week) and float((week > 0).mean()) > 0.5:
            return week
    return pd.to_datetime(frame["date"]).dt.isocalendar().week.astype(int)


def _row_names(sport: str, row) -> tuple[str, str, bool]:
    if sport == "nfl":
        home = str(getattr(row, "home_team_abbr", "") or getattr(row, "home_team", "") or "")
        away = str(getattr(row, "away_team_abbr", "") or getattr(row, "away_team", "") or "")
    else:
        home = str(getattr(row, "home_team", "") or "")
        away = str(getattr(row, "away_team", "") or "")
    neutral = bool(getattr(row, "neutral_site", 0))
    return home, away, neutral


def walk_forward_samples(
    sport: str,
    frame: pd.DataFrame,
    as_of: date,
    build_rankings: Callable[[date], dict[str, Any]],
) -> list[tuple[float, float, int]]:
    """Pregame power gap for each finished game, using rankings from before that week."""
    completed = _completed_games(frame, as_of)
    if completed.empty:
        return []
    completed = completed.copy()
    completed["_bucket"] = _week_bucket(completed)
    samples: list[tuple[float, float, int]] = []
    for _, group in completed.groupby("_bucket", sort=True):
        kickoff = pd.to_datetime(group["date"]).min().date()
        rankings = build_rankings(kickoff)
        ratings = _ratings_from_payload(sport, rankings)
        if not ratings:
            continue
        for row in group.itertuples(index=False):
            home, away, neutral = _row_names(sport, row)
            home_power = _lookup_rating(sport, ratings, home)
            away_power = _lookup_rating(sport, ratings, away)
            if home_power is None or away_power is None:
                continue
            samples.append(
                (
                    home_power - away_power,
                    0.0 if neutral else 1.0,
                    int(float(row.home_score) > float(row.away_score)),
                )
            )
    return samples


def _load_nfl_frame(as_of: date) -> pd.DataFrame:
    from app.ingest.nfl_season_schedule import ensure_season_schedule, load_season_schedule
    from app.models.nfl_baseline import load_games
    from app.services.nfl_power_rankings import _CACHE, _with_current_schedule
    from app.services.power_rankings import schedule_needs_refresh

    season = football_season(as_of)
    schedule = load_season_schedule(season)
    if not schedule or schedule_needs_refresh(schedule, as_of):
        ensure_season_schedule(season, force=True)
    _CACHE.clear()
    return _with_current_schedule(load_games(), as_of)


def _load_cfb_frame(as_of: date) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games
    from app.services.cfb_power_rankings import _CACHE, _with_current_schedule

    _CACHE.clear()
    frame, _conferences = _with_current_schedule(load_games(), as_of)
    return frame


def relearn(sport: str, as_of: date | None = None) -> dict[str, Any]:
    if sport not in PRIORS:
        raise ValueError(f"Unsupported sport: {sport}")
    if as_of is None:
        from app.services.slate_clock import slate_today

        as_of = slate_today()

    if sport == "nfl":
        frame = _load_nfl_frame(as_of)

        def build_rankings(day: date) -> dict[str, Any]:
            from app.services.nfl_power_rankings import build_nfl_power_rankings

            return build_nfl_power_rankings(day, games=frame)

    else:
        frame = _load_cfb_frame(as_of)

        def build_rankings(day: date) -> dict[str, Any]:
            from app.services.cfb_power_rankings import build_cfb_power_rankings

            return build_cfb_power_rankings(day, games=frame)

    samples = walk_forward_samples(sport, frame, as_of, build_rankings)
    fitted = fit_power_win_model(sport, samples)
    current = build_rankings(as_of)
    ratings = _ratings_from_payload(sport, current)
    played = [int(team.get("games_played") or 0) for team in current.get("teams") or []]
    played = [games for games in played if games > 0]
    average_games = (sum(played) / len(played)) if played else 0.0
    artifact = {
        "sport": sport,
        "applied": True,
        "season": int(current.get("season") or football_season(as_of)),
        "as_of": as_of.isoformat(),
        "through": current.get("through"),
        "learned_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "games_fit": int(fitted["games_fit"]),
        "fit_source": fitted["fit_source"],
        "intercept": round(float(fitted["intercept"]), 6),
        "power_coef": round(float(fitted["power_coef"]), 6),
        "home_coef": round(float(fitted["home_coef"]), 6),
        "blend_weight": blend_weight_for_games(average_games),
        "teams_ranked": len(ratings),
        "average_games_played": round(average_games, 2),
        "ratings": {key: round(value, 3) for key, value in sorted(ratings.items())},
    }
    _save(artifact)
    logger.info(
        "%s relearn season=%s games=%s source=%s blend=%s teams=%s",
        sport,
        artifact["season"],
        artifact["games_fit"],
        artifact["fit_source"],
        artifact["blend_weight"],
        artifact["teams_ranked"],
    )
    return artifact


def blend_home_probability(
    sport: str,
    slate_day: date,
    home: str,
    away: str,
    neutral: bool,
    base_probability: float,
) -> tuple[float, dict[str, Any] | None]:
    """Shift one pick toward the saved power-ranking relearn. No-op until relearn runs."""
    artifact = load_relearn(sport)
    if not artifact:
        return float(base_probability), None
    if int(artifact.get("season") or 0) != football_season(slate_day):
        return float(base_probability), None
    ratings = artifact.get("ratings") or {}
    home_power = _lookup_rating(sport, ratings, home)
    away_power = _lookup_rating(sport, ratings, away)
    if home_power is None or away_power is None:
        return float(base_probability), None
    power_probability = power_win_probability(
        float(artifact["intercept"]),
        float(artifact["power_coef"]),
        float(artifact["home_coef"]),
        home_power - away_power,
        neutral=neutral,
    )
    weight = rankings_blend_weight(
        float(artifact.get("blend_weight") or 0.0),
        power_probability,
    )
    mixed = mix_probability(base_probability, power_probability, weight)
    return mixed, {
        "base_model_prob_home": round(float(base_probability), 4),
        "power_prob_home": round(power_probability, 4),
        "relearn_applied": True,
        "relearn_blend_weight": weight,
    }


def board_relearn_note(sport: str, slate_day: date) -> dict[str, Any] | None:
    artifact = load_relearn(sport)
    if not artifact or int(artifact.get("season") or 0) != football_season(slate_day):
        return None
    return public_summary(artifact)
