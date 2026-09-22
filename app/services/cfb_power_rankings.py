"""College football power rankings for FBS teams.

Current-season offense and defense are opponent-adjusted and margin-capped so
one lopsided score cannot set team strength. Last season, roster talent,
returning production, and coaching continuity are the prior, and they fade as
games accumulate. Matchup splits, special teams, and weekly injuries stay
unscored until a feed exists.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd

from app.features.cfb_pregame import conference_tier
from app.odds.cfb_team_aliases import normalize_team_name
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
    zscores,
)

logger = logging.getLogger(__name__)

CFB_HFA = 3.0
CFB_MARGIN_CAP = 28.0
CFB_LATE_GAMES = 12
CFB_LATE_WEIGHT = 0.15
TALENT_POINT_SCALE = 3.0
RETURNING_POINT_SCALE = 2.0
PASSING_POINT_SCALE = 1.5
COACH_CHANGE_PENALTY = 1.5

CFB_UNAVAILABLE = (
    {
        "key": "matchups",
        "label": "Passing and rushing matchups",
        "detail": "Offensive line, pass rush, coverage, and run defense are not scored yet.",
    },
    {
        "key": "special_teams",
        "label": "Special teams",
        "detail": "Kicking, punting, returns, and field position are not scored yet.",
    },
    {
        "key": "availability",
        "label": "Injuries and lineup changes",
        "detail": "Weekly injuries and changes to the starting lineup are not in this rating yet.",
    },
    {
        "key": "game_context",
        "label": "Play-level game context",
        "detail": (
            "Garbage-time plays are not removed yet. Lopsided margins are capped "
            "so one score cannot set team strength."
        ),
    },
)

_CACHE: dict[tuple[str, float], dict] = {}


def _stamp(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return float(pd.Timestamp(value).timestamp())


def _games_before(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    dates = pd.to_datetime(frame["date"]).dt.date
    return frame.loc[dates < as_of].copy()


def _is_ranked_side(conference: str) -> bool:
    return conference_tier(conference) >= 2


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def _fbs_teams(frame: pd.DataFrame, season: int) -> set[str]:
    """FBS teams from conference labels when present, otherwise a full season of games.

    The saved results file often has blank conferences. FCS opponents show up once
    or twice; FBS teams play a full slate, so four games separates them.
    """
    ranked: set[str] = set()
    counts: dict[tuple[int, str], int] = {}
    blocked: set[str] = set()
    if frame.empty or "season" not in frame.columns:
        return ranked
    subset = frame[frame["season"].isin((season, season - 1))]
    for game in subset.itertuples(index=False):
        game_season = int(getattr(game, "season") or 0)
        for side in ("home", "away"):
            team = normalize_team_name(_text(getattr(game, f"{side}_team", "")))
            if not team:
                continue
            conference = _text(getattr(game, f"{side}_conference", ""))
            division = _text(getattr(game, f"{side}_division", "")).lower()
            if division == "fcs" or "fcs" in conference.lower():
                blocked.add(team)
                continue
            if division == "fbs" or _is_ranked_side(conference):
                ranked.add(team)
            counts[(game_season, team)] = counts.get((game_season, team), 0) + 1
    for (game_season, team), played in counts.items():
        needed = 3 if game_season == season else 4
        if played >= needed and team not in blocked:
            ranked.add(team)
    ranked -= blocked
    return ranked


def _conference_names(frame: pd.DataFrame, teams: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    if frame.empty:
        return found
    ordered = frame.sort_values("date")
    for game in ordered.itertuples(index=False):
        home = normalize_team_name(str(getattr(game, "home_team", "") or ""))
        away = normalize_team_name(str(getattr(game, "away_team", "") or ""))
        home_conf = str(getattr(game, "home_conference", "") or "").strip()
        away_conf = str(getattr(game, "away_conference", "") or "").strip()
        if home in teams and home_conf and _is_ranked_side(home_conf):
            found[home] = home_conf
        if away in teams and away_conf and _is_ranked_side(away_conf):
            found[away] = away_conf
    return found


def _season_rows(
    frame: pd.DataFrame,
    season: int,
    ranking_teams: set[str],
) -> list[RawGame]:
    subset = frame[frame["season"] == season]
    if "game_type" in subset.columns:
        subset = subset[subset["game_type"] != "preseason"]
    rows: list[RawGame] = []
    for game in subset.itertuples(index=False):
        home = normalize_team_name(str(getattr(game, "home_team", "") or ""))
        away = normalize_team_name(str(getattr(game, "away_team", "") or ""))
        if not home or not away:
            continue
        if home not in ranking_teams and away not in ranking_teams:
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


def _rate(rows: list[RawGame], ranking_teams: set[str], *, recency: bool) -> dict[str, SideRating]:
    if not rows or not ranking_teams:
        return {team: SideRating() for team in ranking_teams}
    return rate_games(
        rows,
        ranking_teams,
        hfa=CFB_HFA,
        margin_cap=CFB_MARGIN_CAP,
        half_life=HALF_LIFE_GAMES,
        recency=recency,
    )


def _prior_ingredients(
    teams: set[str],
    season: int,
    last: dict[str, SideRating],
) -> dict[str, dict[str, float]]:
    """Point ingredients for the prior. Missing feeds are omitted, not stored as zero."""
    try:
        from app.ingest.cfb_priors import load_priors_store
    except Exception:
        load_priors_store = None  # type: ignore[assignment]
    store = None
    if load_priors_store is not None:
        try:
            store = load_priors_store()
        except Exception:
            store = None

    talent: dict[str, float] = {}
    returning: dict[str, float] = {}
    passing: dict[str, float] = {}
    coach_penalty: dict[str, float] = {}
    if store is not None:
        for team in teams:
            key = normalize_team_name(team)
            if (season, key) in store.talent:
                talent[team] = float(store.talent[(season, key)])
            if (season, key) in store.returning_pct:
                returning[team] = float(store.returning_pct[(season, key)])
            if (season, key) in store.returning_pass_pct:
                passing[team] = float(store.returning_pass_pct[(season, key)])
            coach = store.coaches.get((season, key), "")
            previous = store.coaches.get((season - 1, key), "")
            if coach and previous:
                coach_penalty[team] = COACH_CHANGE_PENALTY if coach != previous else 0.0

    talent_z = zscores(talent)
    returning_z = zscores(returning)
    passing_z = zscores(passing)
    if coach_penalty:
        coach_mean = sum(coach_penalty.values()) / len(coach_penalty)
    else:
        coach_mean = 0.0

    ingredients: dict[str, dict[str, float]] = {}
    for team in teams:
        side = last.get(team) or SideRating()
        parts: dict[str, float] = {"last_season": side.offense + side.defense}
        offense_extra = 0.0
        defense_extra = 0.0
        if team in talent_z:
            talent_points = talent_z[team] * TALENT_POINT_SCALE
            parts["talent"] = talent_points
            offense_extra += talent_points / 2.0
            defense_extra += talent_points / 2.0
        if team in returning_z:
            returning_points = returning_z[team] * RETURNING_POINT_SCALE
            parts["returning"] = returning_points
            offense_extra += returning_points
        if team in passing_z:
            passing_points = passing_z[team] * PASSING_POINT_SCALE
            parts["returning_pass"] = passing_points
            offense_extra += passing_points
        if team in coach_penalty:
            coaching_points = -(coach_penalty[team] - coach_mean)
            parts["coaching"] = coaching_points
            offense_extra += coaching_points / 2.0
            defense_extra += coaching_points / 2.0
        ingredients[team] = {
            "offense": side.offense + offense_extra,
            "defense": side.defense + defense_extra,
            "parts": parts,
        }
    return ingredients


def _logo(team: str) -> str | None:
    try:
        from app.services.cfb_team_logos import lookup_team_logo
    except Exception:
        return None
    meta = lookup_team_logo(team)
    if not meta:
        return None
    url = meta.get("logo_url")
    return str(url) if url else None


def _espn_scores_are_current(season: int, as_of: date) -> bool:
    import json
    from datetime import timedelta

    from app.config import PROJECT_ROOT

    path = PROJECT_ROOT / "data" / "processed" / f"cfb_rankings_scores_{season}.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return str(payload.get("through") or "") >= (as_of - timedelta(days=1)).isoformat()


def _school_name(team_block: dict) -> str:
    display = str(team_block.get("displayName") or team_block.get("name") or "").strip()
    location = str(team_block.get("location") or "").strip()
    normalized = normalize_team_name(display)
    if display and normalized.lower() != display.lower():
        return normalized
    if location:
        return normalize_team_name(location)
    return normalized


def _espn_completed_games(season: int, as_of: date) -> list[dict]:
    """Final FBS scores for the current season from the ESPN scoreboard.

    The season-schedule cache can be stale when CFBD is rate-limited. ESPN is
    the same scoreboard the CFB slate already uses. Results are cached so the
    rankings page does not refetch every day that already has a final.
    """
    import json
    from datetime import timedelta

    from app.config import PROJECT_ROOT
    from app.services.scores_cfb import fetch_cfb_scores_day, live_game_record

    path = PROJECT_ROOT / "data" / "processed" / f"cfb_rankings_scores_{season}.json"
    cached: list[dict] = []
    start = date(season, 8, 20)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        cached = list(payload.get("games") or [])
        through = str(payload.get("through") or "")
        if through >= (as_of - timedelta(days=1)).isoformat():
            return cached
        if through:
            start = date.fromisoformat(through) + timedelta(days=1)
    last = as_of - timedelta(days=1)
    if start > last:
        return cached
    found: list[dict] = []
    day = start
    fetched_through = start - timedelta(days=1)
    while day <= last:
        try:
            events = fetch_cfb_scores_day(day)
        except Exception as exc:
            logger.warning("CFB rankings score fetch failed for %s: %s", day, exc)
            break
        for event in events:
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors") or []
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            parsed = live_game_record(event)
            if parsed.get("status") != "Final":
                continue
            if parsed.get("home_score") is None or parsed.get("away_score") is None:
                continue
            if parsed["home_score"] == parsed["away_score"]:
                continue
            found.append(
                {
                    "game_id": f"espn-{parsed.get('game_id')}",
                    "date": day.isoformat(),
                    "season": season,
                    "home_team": _school_name(home.get("team") or {}),
                    "away_team": _school_name(away.get("team") or {}),
                    "home_score": parsed["home_score"],
                    "away_score": parsed["away_score"],
                    "neutral_site": parsed.get("neutral_site") or 0,
                    "completed": True,
                }
            )
        fetched_through = day
        day += timedelta(days=1)
    if fetched_through < start:
        return cached
    merged: dict[tuple[str, str, str], dict] = {}
    for game in cached + found:
        key = (str(game.get("date")), str(game.get("home_team")), str(game.get("away_team")))
        merged[key] = game
    games = list(merged.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"season": season, "through": fetched_through.isoformat(), "games": games}, indent=2),
        encoding="utf-8",
    )
    logger.info("Cached %s completed CFB games for %s through %s", len(games), season, fetched_through)
    return games


def _with_current_schedule(games: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, dict[str, str]]:
    """Add this season's completed games and conference names from the FBS schedule."""
    season = football_season(as_of)
    conferences: dict[str, str] = {}
    try:
        from app.ingest.cfb_season_schedule import (
            ensure_season_schedule,
            load_season_schedule,
        )
    except Exception:
        return games, conferences
    schedule = load_season_schedule(season)
    if schedule_needs_refresh(schedule, as_of) and not _espn_scores_are_current(season, as_of):
        try:
            schedule = ensure_season_schedule(season, force=True)
        except Exception as exc:
            logger.warning("CFB schedule refresh for power rankings failed: %s", exc)
    if not schedule:
        schedule = []
    existing = set(games["game_id"].astype(str)) if "game_id" in games.columns else set()
    rows = []
    for game in schedule:
        home = normalize_team_name(str(game.get("home_team") or ""))
        away = normalize_team_name(str(game.get("away_team") or ""))
        home_conf = str(game.get("home_conference") or "").strip()
        away_conf = str(game.get("away_conference") or "").strip()
        home_div = str(game.get("home_division") or "").lower()
        away_div = str(game.get("away_division") or "").lower()
        if home and home_conf and (home_div == "fbs" or _is_ranked_side(home_conf)):
            conferences[home] = home_conf
        if away and away_conf and (away_div == "fbs" or _is_ranked_side(away_conf)):
            conferences[away] = away_conf
        game_id = str(game.get("game_id") or "")
        if not game_id or game_id in existing:
            continue
        if not game.get("completed") or game.get("home_score") is None or game.get("away_score") is None:
            continue
        if str(game.get("date") or "") >= as_of.isoformat():
            continue
        rows.append(
            {
                "game_id": game_id,
                "date": game.get("date"),
                "season": season,
                "game_type": "regular",
                "home_team": home,
                "away_team": away,
                "home_score": game.get("home_score"),
                "away_score": game.get("away_score"),
                "neutral_site": game.get("neutral_site") or 0,
                "home_conference": home_conf,
                "away_conference": away_conf,
                "home_division": home_div,
                "away_division": away_div,
            }
        )
    if not rows:
        for game in _espn_completed_games(season, as_of):
            home = normalize_team_name(str(game.get("home_team") or ""))
            away = normalize_team_name(str(game.get("away_team") or ""))
            if not home or not away:
                continue
            rows.append(
                {
                    "game_id": game.get("game_id"),
                    "date": game.get("date"),
                    "season": season,
                    "game_type": "regular",
                    "home_team": home,
                    "away_team": away,
                    "home_score": game.get("home_score"),
                    "away_score": game.get("away_score"),
                    "neutral_site": game.get("neutral_site") or 0,
                    "home_conference": conferences.get(home, ""),
                    "away_conference": conferences.get(away, ""),
                    "home_division": "fbs" if home in conferences else "",
                    "away_division": "fbs" if away in conferences else "",
                }
            )
    if not rows:
        return games, conferences
    extra = pd.DataFrame(rows)
    extra["date"] = pd.to_datetime(extra["date"])
    return pd.concat([games, extra], ignore_index=True), conferences


def build_cfb_power_rankings(
    as_of: date | None = None,
    games: pd.DataFrame | None = None,
) -> dict:
    if as_of is None:
        from app.services.slate_clock import slate_today

        as_of = slate_today()
    if games is None:
        from app.ingest.cfb_season_schedule import season_schedule_path
        from app.models.cfb_baseline import PARQUET_PATH, load_games

        games, schedule_conferences = _with_current_schedule(load_games(), as_of)
        schedule_path = season_schedule_path(football_season(as_of))
        schedule_mtime = schedule_path.stat().st_mtime if schedule_path.exists() else 0.0
        parquet_mtime = PARQUET_PATH.stat().st_mtime if PARQUET_PATH.exists() else 0.0
        cache_key = (as_of.isoformat(), parquet_mtime, schedule_mtime)
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        payload = _build(as_of, games, conferences=schedule_conferences)
        _CACHE.clear()
        _CACHE[cache_key] = payload
        return payload
    return _build(as_of, games)


def _build(
    as_of: date,
    games: pd.DataFrame,
    conferences: dict[str, str] | None = None,
) -> dict:
    history = _games_before(games, as_of)
    season = football_season(as_of)
    ranking_teams = _fbs_teams(history, season)
    current = _season_rows(history, season, ranking_teams)
    previous = _season_rows(history, season - 1, ranking_teams)
    season_rating = _rate(current, ranking_teams, recency=True)
    flat_rating = _rate(current, ranking_teams, recency=False)
    prior_rating = _rate(previous, ranking_teams, recency=True)
    ingredients = _prior_ingredients(ranking_teams, season, prior_rating)
    group_names = _conference_names(history, ranking_teams)
    if conferences:
        group_names.update({team: name for team, name in conferences.items() if team in ranking_teams})

    blended_offense: dict[str, float] = {}
    blended_defense: dict[str, float] = {}
    composed: dict[str, tuple] = {}
    weights: dict[str, float] = {}
    for team in ranking_teams:
        season_side = season_rating.get(team) or SideRating()
        flat_side = flat_rating.get(team) or SideRating()
        ingredient = ingredients.get(team) or {"offense": 0.0, "defense": 0.0, "parts": {}}
        weight = prior_weight_for_games(
            season_side.games,
            late_weight=CFB_LATE_WEIGHT,
            late_games=CFB_LATE_GAMES,
        )
        part_pairs = [(key, float(value)) for key, value in ingredient["parts"].items()]
        score = compose_score(
            season_side,
            flat_side,
            prior_offense=float(ingredient["offense"]),
            prior_defense=float(ingredient["defense"]),
            prior_weight=weight,
            prior_parts=part_pairs,
        )
        composed[team] = (score, season_side)
        weights[team] = weight
        blended_offense[team] = from_tenths(score.offense_tenths)
        blended_defense[team] = from_tenths(score.defense_tenths)

    offense_ranks = rank_map(blended_offense)
    defense_ranks = rank_map(blended_defense)
    team_count = len(ranking_teams)
    teams = []
    for team in ranking_teams:
        score, season_side = composed[team]
        teams.append(
            {
                "team": team,
                "abbr": "",
                "group": group_names.get(team, ""),
                "logo_url": _logo(team),
                "power": from_tenths(score.power_tenths),
                "offense_rating": blended_offense[team],
                "defense_rating": blended_defense[team],
                "offense_rank": offense_ranks[team],
                "defense_rank": defense_ranks[team],
                "games_played": season_side.games,
                "wins": season_side.wins,
                "losses": season_side.games - season_side.wins,
                "prior_weight": round(weights[team], 2),
                "subpoints": assemble_subpoints(
                    score,
                    team_count=team_count,
                    offense_rank=offense_ranks[team],
                    defense_rank=defense_ranks[team],
                    prior_weight=weights[team],
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

    groups = sorted({row["group"] for row in teams if row["group"]})
    return {
        "sport": "cfb",
        "as_of": as_of.isoformat(),
        "season": season,
        "through": through,
        "team_count": team_count,
        "unit": "points versus an average FBS team",
        "groups": groups,
        "group_label": "Conference",
        "unavailable": list(CFB_UNAVAILABLE),
        "summary": (
            "Offense and defense are opponent-adjusted points against the defenses and "
            "offenses each team has faced. Strength of schedule is already inside those "
            "numbers. Talent, returning production, and coaching continuity start as the "
            "preseason estimate and fade as this year's games accumulate. Margins are "
            "capped so a lopsided score cannot set the rating."
        ),
        "teams": teams,
    }
