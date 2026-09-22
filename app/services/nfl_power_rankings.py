"""NFL power rankings from completed games only.

In-season offense and defense use regular-season results before the slate date.
Last season is the prior. Preseason games are a small part of that prior, not
the current-season rating. Quarterback, matchup, special teams, and injury
factors are listed and left unscored until a feed exists.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from app.ingest.nfl import NFL_DIVISIONS, normalize_abbr
from app.services.power_rankings import (
    HALF_LIFE_GAMES,
    RawGame,
    SideRating,
    assemble_subpoints,
    compose_score,
    football_season,
    from_tenths,
    prior_weight_for_games,
    rank_map,
    rate_games,
    schedule_needs_refresh,
)

logger = logging.getLogger(__name__)

NFL_HFA = 2.4
NFL_MARGIN_CAP = 21.0
NFL_LATE_GAMES = 8
NFL_LATE_WEIGHT = 0.30
PRESEASON_PRIOR_SHARE = 0.20

NFL_UNAVAILABLE = (
    {
        "key": "quarterback",
        "label": "Quarterback play",
        "detail": "Current starter, backup quality, and quarterback changes are not in the team rating yet.",
    },
    {
        "key": "passing",
        "label": "Passing matchup strength",
        "detail": "Pass protection, receiving options, pass rush, and coverage are not scored yet.",
    },
    {
        "key": "rushing",
        "label": "Rushing and situational performance",
        "detail": "Run game, run defense, third downs, and red zone are not scored yet.",
    },
    {
        "key": "special_teams",
        "label": "Special teams",
        "detail": "Kicking, punting, returns, and field position are not scored yet.",
    },
    {
        "key": "availability",
        "label": "Roster availability",
        "detail": "Injuries, suspensions, and returns are not weighted into the team rating yet.",
    },
)

_CACHE: dict[tuple[str, float], dict] = {}


def _logo(abbr: str) -> str:
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{abbr.lower()}.png"


def _division_label(division: str) -> str:
    return division.replace("_", " ")


def _stamp(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    parsed = pd.Timestamp(value)
    return float(parsed.timestamp())


def _games_before(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    dates = pd.to_datetime(frame["date"]).dt.date
    return frame.loc[dates < as_of].copy()


def _season_rows(frame: pd.DataFrame, season: int, *, preseason: bool) -> list[RawGame]:
    subset = frame[frame["season"] == season]
    if preseason:
        subset = subset[subset["game_type"] == "preseason"]
    else:
        subset = subset[subset["game_type"] != "preseason"]
    rows: list[RawGame] = []
    for game in subset.itertuples(index=False):
        home = normalize_abbr(getattr(game, "home_team_abbr", "") or getattr(game, "home_team", ""))
        away = normalize_abbr(getattr(game, "away_team_abbr", "") or getattr(game, "away_team", ""))
        if home not in NFL_DIVISIONS or away not in NFL_DIVISIONS:
            continue
        when = _stamp(game.date)
        neutral = bool(getattr(game, "neutral_site", 0))
        home_score = float(game.home_score)
        away_score = float(game.away_score)
        rows.append(
            RawGame(
                team=home,
                opponent=away,
                points_for=home_score,
                points_against=away_score,
                is_home=True,
                neutral=neutral,
                when=when,
                win=int(home_score > away_score),
            )
        )
        rows.append(
            RawGame(
                team=away,
                opponent=home,
                points_for=away_score,
                points_against=home_score,
                is_home=False,
                neutral=neutral,
                when=when,
                win=int(away_score > home_score),
            )
        )
    return rows


def _names(frame: pd.DataFrame) -> dict[str, str]:
    names: dict[str, str] = {}
    ordered = frame.sort_values("date")
    for game in ordered.itertuples(index=False):
        home = normalize_abbr(getattr(game, "home_team_abbr", ""))
        away = normalize_abbr(getattr(game, "away_team_abbr", ""))
        if home in NFL_DIVISIONS and getattr(game, "home_team", ""):
            names[home] = str(game.home_team)
        if away in NFL_DIVISIONS and getattr(game, "away_team", ""):
            names[away] = str(game.away_team)
    for abbr in NFL_DIVISIONS:
        names.setdefault(abbr, abbr)
    return names


def _empty_rating() -> SideRating:
    return SideRating()


def _rate(rows: list[RawGame], *, recency: bool) -> dict[str, SideRating]:
    if not rows:
        return {abbr: _empty_rating() for abbr in NFL_DIVISIONS}
    return rate_games(
        rows,
        set(NFL_DIVISIONS),
        hfa=NFL_HFA,
        margin_cap=NFL_MARGIN_CAP,
        half_life=HALF_LIFE_GAMES,
        recency=recency,
    )


def _with_current_schedule(games: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Add this season's completed games when the training parquet is behind."""
    season = football_season(as_of)
    try:
        from app.ingest.nfl_season_schedule import (
            ensure_season_schedule,
            load_season_schedule,
        )
    except Exception:
        return games
    schedule = load_season_schedule(season)
    if schedule_needs_refresh(schedule, as_of):
        try:
            schedule = ensure_season_schedule(season, force=True)
        except Exception as exc:
            logger.warning("NFL schedule refresh for power rankings failed: %s", exc)
    if not schedule:
        return games
    existing = set(games["game_id"].astype(str)) if "game_id" in games.columns else set()
    rows = []
    for game in schedule:
        game_id = str(game.get("game_id") or "")
        if not game_id or game_id in existing:
            continue
        if not game.get("completed") or game.get("tie"):
            continue
        if game.get("home_score") is None or game.get("away_score") is None:
            continue
        if str(game.get("date") or "") >= as_of.isoformat():
            continue
        rows.append(
            {
                "game_id": game_id,
                "date": game.get("date"),
                "season": season,
                "game_type": "regular",
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "home_team_abbr": game.get("home_team_abbr"),
                "away_team_abbr": game.get("away_team_abbr"),
                "home_score": game.get("home_score"),
                "away_score": game.get("away_score"),
                "neutral_site": game.get("neutral_site") or 0,
            }
        )
    if not rows:
        return games
    extra = pd.DataFrame(rows)
    extra["date"] = pd.to_datetime(extra["date"])
    return pd.concat([games, extra], ignore_index=True)


def build_nfl_power_rankings(
    as_of: date | None = None,
    games: pd.DataFrame | None = None,
) -> dict:
    if as_of is None:
        from app.services.slate_clock import slate_today

        as_of = slate_today()
    if games is None:
        from app.ingest.nfl_season_schedule import season_schedule_path
        from app.models.nfl_baseline import PARQUET_PATH, load_games

        games = _with_current_schedule(load_games(), as_of)
        schedule_mtime = (
            season_schedule_path(football_season(as_of)).stat().st_mtime
            if season_schedule_path(football_season(as_of)).exists()
            else 0.0
        )
        parquet_mtime = PARQUET_PATH.stat().st_mtime if PARQUET_PATH.exists() else 0.0
        cache_key = (as_of.isoformat(), parquet_mtime, schedule_mtime)
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        payload = _build(as_of, games)
        _CACHE.clear()
        _CACHE[cache_key] = payload
        return payload
    return _build(as_of, games)


def _build(as_of: date, games: pd.DataFrame) -> dict:
    history = _games_before(games, as_of)
    if history.empty or "season" not in history.columns:
        season = football_season(as_of)
        history = history.iloc[0:0]
    else:
        season = football_season(as_of)
    current = _season_rows(history, season, preseason=False)
    preseason = _season_rows(history, season, preseason=True)
    previous = _season_rows(history, season - 1, preseason=False)
    season_rating = _rate(current, recency=True)
    flat_rating = _rate(current, recency=False)
    prior_rating = _rate(previous, recency=True)
    preseason_rating = _rate(preseason, recency=True)
    names = _names(history if not history.empty else games)

    blended_offense: dict[str, float] = {}
    blended_defense: dict[str, float] = {}
    composed: dict[str, object] = {}
    weights: dict[str, float] = {}
    for abbr in NFL_DIVISIONS:
        season_side = season_rating.get(abbr) or _empty_rating()
        flat_side = flat_rating.get(abbr) or _empty_rating()
        last = prior_rating.get(abbr) or _empty_rating()
        pre = preseason_rating.get(abbr) or _empty_rating()
        weight = prior_weight_for_games(
            season_side.games,
            late_weight=NFL_LATE_WEIGHT,
            late_games=NFL_LATE_GAMES,
        )
        if pre.games:
            prior_offense = (1.0 - PRESEASON_PRIOR_SHARE) * last.offense + PRESEASON_PRIOR_SHARE * pre.offense
            prior_defense = (1.0 - PRESEASON_PRIOR_SHARE) * last.defense + PRESEASON_PRIOR_SHARE * pre.defense
            prior_parts = [
                ("last_season", (1.0 - PRESEASON_PRIOR_SHARE) * (last.offense + last.defense)),
                ("preseason", PRESEASON_PRIOR_SHARE * (pre.offense + pre.defense)),
            ]
        else:
            prior_offense = last.offense
            prior_defense = last.defense
            prior_parts = [("last_season", last.offense + last.defense)]
        score = compose_score(
            season_side,
            flat_side,
            prior_offense=prior_offense,
            prior_defense=prior_defense,
            prior_weight=weight,
            prior_parts=prior_parts,
        )
        composed[abbr] = (score, season_side)
        weights[abbr] = weight
        blended_offense[abbr] = from_tenths(score.offense_tenths)
        blended_defense[abbr] = from_tenths(score.defense_tenths)

    offense_ranks = rank_map(blended_offense)
    defense_ranks = rank_map(blended_defense)
    team_count = len(NFL_DIVISIONS)
    teams = []
    for abbr in NFL_DIVISIONS:
        score, season_side = composed[abbr]
        division = NFL_DIVISIONS[abbr]
        teams.append(
            {
                "team": names.get(abbr, abbr),
                "abbr": abbr,
                "group": _division_label(division),
                "logo_url": _logo(abbr),
                "power": from_tenths(score.power_tenths),
                "offense_rating": blended_offense[abbr],
                "defense_rating": blended_defense[abbr],
                "offense_rank": offense_ranks[abbr],
                "defense_rank": defense_ranks[abbr],
                "games_played": season_side.games,
                "wins": season_side.wins,
                "losses": season_side.games - season_side.wins,
                "prior_weight": round(weights[abbr], 2),
                "subpoints": assemble_subpoints(
                    score,
                    team_count=team_count,
                    offense_rank=offense_ranks[abbr],
                    defense_rank=defense_ranks[abbr],
                    prior_weight=weights[abbr],
                    games_played=season_side.games,
                ),
            }
        )
    teams.sort(key=lambda row: (-row["power"], row["team"]))
    for index, row in enumerate(teams, start=1):
        row["rank"] = index

    through = None
    if not history.empty:
        through_dates = pd.to_datetime(history["date"])
        current_dates = through_dates[history["season"] == season]
        latest = current_dates.max() if len(current_dates) else through_dates.max()
        if pd.notna(latest):
            through = pd.Timestamp(latest).date().isoformat()

    groups = []
    seen: set[str] = set()
    for division in NFL_DIVISIONS.values():
        label = _division_label(division)
        if label not in seen:
            seen.add(label)
            groups.append(label)

    return {
        "sport": "nfl",
        "as_of": as_of.isoformat(),
        "season": season,
        "through": through,
        "team_count": team_count,
        "unit": "points versus an average team",
        "groups": groups,
        "group_label": "Division",
        "unavailable": list(NFL_UNAVAILABLE),
        "summary": (
            "Offense and defense are opponent-adjusted points from this season's games. "
            "Newer games count more. Last season, plus a light preseason blend, fades as "
            "the regular season builds. Schedule strength is already inside the efficiency numbers."
        ),
        "teams": teams,
    }
