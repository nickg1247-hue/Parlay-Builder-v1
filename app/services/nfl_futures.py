"""NFL year-end division standings futures.

Wednesday snapshot: lock completed regular-season results, project the rest
of the season with the active moneyline model, then rank every division 1–4.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.ingest.nfl import NFL_DIVISIONS, normalize_abbr, team_division
from app.services.nfl_division_priors import annotate_division, annotate_futures_payload
from app.services.nfl_slate_predictions import nfl_season_year

logger = logging.getLogger(__name__)

FUTURES_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_futures.json"
N_SIMS = 800

DIVISION_SPECS: tuple[dict[str, str], ...] = (
    {"key": "AFC_EAST", "conference": "AFC", "name": "AFC East"},
    {"key": "AFC_NORTH", "conference": "AFC", "name": "AFC North"},
    {"key": "AFC_SOUTH", "conference": "AFC", "name": "AFC South"},
    {"key": "AFC_WEST", "conference": "AFC", "name": "AFC West"},
    {"key": "NFC_EAST", "conference": "NFC", "name": "NFC East"},
    {"key": "NFC_NORTH", "conference": "NFC", "name": "NFC North"},
    {"key": "NFC_SOUTH", "conference": "NFC", "name": "NFC South"},
    {"key": "NFC_WEST", "conference": "NFC", "name": "NFC West"},
)

PLACE_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
DISCLAIMER = (
    "Wednesday snapshot. A named winner is only a pick when the race is "
    "Clear or a Lean — toss-up divisions are too close to fade or follow. "
    "Preseason ranks use a three-year win prior plus the upcoming schedule. "
    "After Week 1, completed results lock and the live model blends in."
)


def wednesday_week_id(as_of: date) -> date:
    """Most recent Wednesday (today if Wednesday). Futures reset on this day."""
    days_since_wednesday = (as_of.weekday() - 2) % 7
    return as_of - timedelta(days=days_since_wednesday)


def current_nfl_season(as_of: date | None = None) -> int:
    return nfl_season_year(as_of or date.today())


def nfl_logo_url(abbr: str | None, espn_logo: str | None = None) -> str:
    if espn_logo:
        return str(espn_logo)
    key = normalize_abbr(abbr).lower()
    if not key:
        return ""
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{key}.png"


def _clip_prob(value: float) -> float:
    return float(min(0.97, max(0.03, value)))


def split_completed_remaining(
    games: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    completed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for game in games:
        if game.get("tie") or game.get("home_win") is not None:
            completed.append(game)
        else:
            remaining.append(game)
    return completed, remaining


def actual_records(completed: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    records: dict[str, dict[str, float]] = defaultdict(
        lambda: {"wins": 0.0, "losses": 0.0, "ties": 0.0}
    )
    for game in completed:
        home = normalize_abbr(game.get("home_team_abbr"))
        away = normalize_abbr(game.get("away_team_abbr"))
        if not home or not away:
            continue
        if game.get("tie"):
            records[home]["ties"] += 1
            records[away]["ties"] += 1
            continue
        if int(game["home_win"]):
            records[home]["wins"] += 1
            records[away]["losses"] += 1
        else:
            records[away]["wins"] += 1
            records[home]["losses"] += 1
    return records


def _team_meta(games: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    for game in games:
        for side in ("home", "away"):
            abbr = normalize_abbr(game.get(f"{side}_team_abbr"))
            if not abbr:
                continue
            name = str(game.get(f"{side}_team") or "").strip()
            logo = game.get(f"{side}_logo_url")
            row = meta.setdefault(
                abbr,
                {"abbr": abbr, "team": name or abbr, "logo_url": nfl_logo_url(abbr)},
            )
            if name:
                row["team"] = name
            if logo:
                row["logo_url"] = nfl_logo_url(abbr, str(logo))
    for abbr, div in NFL_DIVISIONS.items():
        meta.setdefault(
            abbr,
            {"abbr": abbr, "team": abbr, "logo_url": nfl_logo_url(abbr), "division": div},
        )
        meta[abbr]["division"] = team_division(abbr)
    return meta


def overlay_completed_from_ingest(
    schedule: list[dict[str, Any]],
    season: int,
) -> list[dict[str, Any]]:
    """Prefer local ingest scores when a regular-season game is already in parquet."""
    try:
        from app.models.nfl_baseline import load_games

        hist = load_games()
    except FileNotFoundError:
        return [dict(g) for g in schedule]
    if hist.empty or "home_win" not in hist.columns:
        return [dict(g) for g in schedule]
    hist = hist.copy()
    if "game_type" in hist.columns:
        hist = hist[hist["game_type"].fillna("regular") == "regular"]
    hist = hist[hist["season"] == int(season)]
    hist = hist[hist["home_win"].notna()]
    by_id: dict[str, Any] = {}
    for row in hist.itertuples(index=False):
        by_id[str(row.game_id)] = row

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for game in schedule:
        row_out = dict(game)
        gid = str(row_out.get("game_id") or "")
        seen.add(gid)
        row = by_id.get(gid)
        if row is not None:
            home_score = getattr(row, "home_score", None)
            away_score = getattr(row, "away_score", None)
            row_out["completed"] = True
            row_out["home_score"] = None if pd.isna(home_score) else int(home_score)
            row_out["away_score"] = None if pd.isna(away_score) else int(away_score)
            if (
                row_out["home_score"] is not None
                and row_out["away_score"] is not None
                and row_out["home_score"] == row_out["away_score"]
            ):
                row_out["tie"] = True
                row_out["home_win"] = None
            else:
                row_out["tie"] = False
                row_out["home_win"] = int(row.home_win)
        out.append(row_out)

    for gid, row in by_id.items():
        if gid in seen:
            continue
        home_abbr = normalize_abbr(getattr(row, "home_team_abbr", ""))
        away_abbr = normalize_abbr(getattr(row, "away_team_abbr", ""))
        home_score = getattr(row, "home_score", None)
        away_score = getattr(row, "away_score", None)
        tie = (
            home_score is not None
            and away_score is not None
            and not pd.isna(home_score)
            and not pd.isna(away_score)
            and int(home_score) == int(away_score)
        )
        out.append(
            {
                "game_id": gid,
                "date": pd.Timestamp(row.date).strftime("%Y-%m-%d"),
                "season": int(row.season),
                "week": int(getattr(row, "week", 0) or 0),
                "game_type": "regular",
                "home_team": str(getattr(row, "home_team", "") or home_abbr),
                "away_team": str(getattr(row, "away_team", "") or away_abbr),
                "home_team_abbr": home_abbr,
                "away_team_abbr": away_abbr,
                "divisional": int(getattr(row, "divisional", 0) or 0),
                "neutral_site": int(getattr(row, "neutral_site", 0) or 0),
                "completed": True,
                "home_score": None if pd.isna(home_score) else int(home_score),
                "away_score": None if pd.isna(away_score) else int(away_score),
                "home_win": None if tie else int(row.home_win),
                "tie": tie,
            }
        )
    out.sort(key=lambda g: (str(g.get("date") or ""), str(g.get("game_id") or "")))
    return out


def project_from_probs(
    *,
    records: dict[str, dict[str, float]],
    remaining: list[dict[str, Any]],
    probs: dict[str, float],
    strength: dict[str, float],
    team_meta: dict[str, dict[str, str]],
    n_sims: int = N_SIMS,
    seed: int = 0,
) -> dict[str, list[dict[str, Any]]]:
    """Seeded Monte Carlo of remaining games → division standings."""
    teams = sorted(NFL_DIVISIONS)
    team_index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)
    rng = np.random.default_rng(seed)

    wins = np.zeros((n_sims, n_teams), dtype=np.float64)
    for team, rec in records.items():
        idx = team_index.get(team)
        if idx is None:
            continue
        wins[:, idx] = rec["wins"] + 0.5 * rec["ties"]

    for game in remaining:
        gid = str(game["game_id"])
        p_home = _clip_prob(float(probs.get(gid, 0.5)))
        home = normalize_abbr(game.get("home_team_abbr"))
        away = normalize_abbr(game.get("away_team_abbr"))
        if home not in team_index or away not in team_index:
            continue
        draws = rng.random(n_sims) < p_home
        wins[draws, team_index[home]] += 1
        wins[~draws, team_index[away]] += 1

    finish_sum = np.zeros(n_teams, dtype=np.float64)
    place_counts = np.zeros((n_teams, 4), dtype=np.int32)
    strength_arr = np.array([float(strength.get(team, 1500.0)) for team in teams])

    teams_by_div: dict[str, list[int]] = defaultdict(list)
    for team, div in NFL_DIVISIONS.items():
        teams_by_div[div].append(team_index[team])

    for sim in range(n_sims):
        for idxs in teams_by_div.values():
            order = sorted(
                idxs,
                key=lambda i: (-wins[sim, i], -strength_arr[i], teams[i]),
            )
            for place, idx in enumerate(order, start=1):
                finish_sum[idx] += place
                if 1 <= place <= 4:
                    place_counts[idx, place - 1] += 1

    expected_wins = wins.mean(axis=0)
    mean_finish = finish_sum / n_sims
    place_pct = place_counts / n_sims

    standings: dict[str, list[dict[str, Any]]] = {}
    for spec in DIVISION_SPECS:
        idxs = teams_by_div.get(spec["key"]) or []
        ranked = sorted(
            idxs,
            key=lambda i: (
                mean_finish[i],
                -place_pct[i, 0],
                -expected_wins[i],
                -strength_arr[i],
                teams[i],
            ),
        )
        rows = []
        for place, idx in enumerate(ranked, start=1):
            team = teams[idx]
            rec = records.get(team, {"wins": 0.0, "losses": 0.0, "ties": 0.0})
            meta = team_meta.get(team) or {}
            rows.append(
                {
                    "place": place,
                    "place_label": PLACE_LABELS.get(place, str(place)),
                    "team": meta.get("team") or team,
                    "abbr": team,
                    "logo_url": meta.get("logo_url") or nfl_logo_url(team),
                    "division_key": spec["key"],
                    "actual_wins": int(rec["wins"]),
                    "actual_losses": int(rec["losses"]),
                    "actual_ties": int(rec["ties"]),
                    "expected_wins": round(float(expected_wins[idx]), 2),
                    "mean_finish": round(float(mean_finish[idx]), 2),
                    "division_win_pct": round(float(place_pct[idx, 0]), 4),
                    "place_pct": {
                        "1": round(float(place_pct[idx, 0]), 4),
                        "2": round(float(place_pct[idx, 1]), 4),
                        "3": round(float(place_pct[idx, 2]), 4),
                        "4": round(float(place_pct[idx, 3]), 4),
                    },
                    "strength": round(float(strength_arr[idx]), 1),
                }
            )
        standings[spec["key"]] = rows
    return standings


def _slate_frame(games: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for game in games:
        home_abbr = normalize_abbr(game.get("home_team_abbr"))
        away_abbr = normalize_abbr(game.get("away_team_abbr"))
        rows.append(
            {
                "game_id": str(game["game_id"]),
                "date": game["date"],
                "season": int(game["season"]),
                "week": int(game.get("week") or 0),
                "game_type": "regular",
                "home_team": game.get("home_team") or home_abbr,
                "away_team": game.get("away_team") or away_abbr,
                "home_team_abbr": home_abbr,
                "away_team_abbr": away_abbr,
                "divisional": int(game.get("divisional") or 0),
                "neutral_site": int(game.get("neutral_site") or 0),
            }
        )
    return pd.DataFrame(rows)


def predict_remaining_probs(
    remaining: list[dict[str, Any]],
    completed: list[dict[str, Any]] | None = None,
    *,
    season: int | None = None,
) -> tuple[dict[str, float], dict[str, float], str]:
    """Offseason prior, blended with the live model after games start."""
    from app.models.nfl_baseline import load_games, predict_home_win_proba
    from app.services.nfl_division_priors import (
        prior_game_probs,
        prior_mix_weight,
        projected_wins_from_history,
        wins_to_elo,
    )

    completed = completed or []
    if season is None:
        if remaining:
            season = int(remaining[0]["season"])
        elif completed:
            season = int(completed[0]["season"])
        else:
            season = current_nfl_season()

    try:
        hist = load_games()
    except FileNotFoundError:
        hist = pd.DataFrame()

    prior_wins = projected_wins_from_history(hist, int(season)) if not hist.empty else {}
    prior_elo = wins_to_elo(prior_wins) if prior_wins else {}
    prior_probs = prior_game_probs(remaining, prior_elo) if remaining else {}
    mix = prior_mix_weight(len(completed), len(remaining))
    if not remaining:
        return {}, prior_elo, "none"
    if mix >= 0.999 or not prior_probs:
        if prior_probs:
            return prior_probs, prior_elo, "offseason_prior_3y"
        return prior_probs, prior_elo, "none"

    model_probs: dict[str, float] = {}
    model_name = "elo_fallback"
    try:
        slate = _slate_frame(remaining)
        raw = predict_home_win_proba(slate)
        model_probs = {
            str(gid): _clip_prob(float(p))
            for gid, p in zip(slate["game_id"].astype(str), raw)
        }
        model_name = "v2_gbr_elo_tossup"
    except Exception as exc:
        logger.warning("NFL futures model probs failed, using prior: %s", exc)
        return prior_probs, prior_elo, "offseason_prior_3y"

    blended = {}
    for game in remaining:
        gid = str(game["game_id"])
        blended[gid] = _clip_prob(
            mix * float(prior_probs.get(gid, 0.5))
            + (1.0 - mix) * float(model_probs.get(gid, 0.5))
        )
    return blended, prior_elo, f"offseason_prior_3y+{model_name}"


def load_saved_nfl_futures() -> dict[str, Any] | None:
    if not FUTURES_JSON.exists():
        return None
    try:
        payload = json.loads(FUTURES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def cache_is_current(payload: dict[str, Any] | None, *, as_of: date, season: int) -> bool:
    if not payload:
        return False
    return (
        str(payload.get("week_id")) == as_of.isoformat()
        and int(payload.get("season") or 0) == int(season)
        and bool(payload.get("divisions"))
    )


def _empty_payload(*, season: int, as_of: date, error: str) -> dict[str, Any]:
    return {
        "sport": "nfl",
        "season": season,
        "week_id": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": None,
        "n_sims": N_SIMS,
        "games_completed": 0,
        "games_remaining": 0,
        "disclaimer": DISCLAIMER,
        "divisions": [],
        "error": error,
    }


def _division_payloads(standings: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for spec in DIVISION_SPECS:
        rows = standings.get(spec["key"]) or []
        if not rows:
            continue
        out.append(
            annotate_division(
                {
                    "key": spec["key"],
                    "name": spec["name"],
                    "conference": spec["conference"],
                    "champion": rows[0]["team"],
                    "champion_abbr": rows[0]["abbr"],
                    "teams": rows,
                }
            )
        )
    return out


def build_nfl_futures(
    *,
    season: int | None = None,
    as_of: date | None = None,
    refresh: bool = False,
    n_sims: int = N_SIMS,
    games: list[dict[str, Any]] | None = None,
    probs: dict[str, float] | None = None,
    strength: dict[str, float] | None = None,
    write_cache: bool = True,
) -> dict[str, Any]:
    today = as_of or date.today()
    week_id = wednesday_week_id(today)
    season_year = int(season or current_nfl_season(today))

    if not refresh:
        saved = load_saved_nfl_futures()
        if cache_is_current(saved, as_of=week_id, season=season_year):
            return annotate_futures_payload(saved)  # type: ignore[return-value]

    if games is None:
        from app.ingest.nfl_season_schedule import ensure_season_schedule

        try:
            games = ensure_season_schedule(season_year, force=refresh)
        except Exception as exc:
            logger.warning("NFL season schedule fetch failed: %s", exc)
            payload = _empty_payload(
                season=season_year,
                as_of=week_id,
                error=f"Could not load {season_year} NFL regular-season schedule: {exc}",
            )
            if write_cache:
                FUTURES_JSON.parent.mkdir(parents=True, exist_ok=True)
                FUTURES_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload
        games = overlay_completed_from_ingest(games, season_year)

    if not games:
        return _empty_payload(
            season=season_year,
            as_of=week_id,
            error=f"No {season_year} NFL regular-season schedule games yet.",
        )

    games = [dict(g) for g in games]
    team_meta = _team_meta(games)
    completed, remaining = split_completed_remaining(games)
    records = actual_records(completed)

    model_name = "injected"
    if probs is None or strength is None:
        probs, strength, model_name = predict_remaining_probs(
            remaining, completed, season=season_year
        )
    assert probs is not None
    assert strength is not None

    seed = int(week_id.strftime("%Y%m%d"))
    standings = project_from_probs(
        records=records,
        remaining=remaining,
        probs=probs,
        strength=strength,
        team_meta=team_meta,
        n_sims=n_sims,
        seed=seed,
    )
    payload = {
        "sport": "nfl",
        "season": season_year,
        "week_id": week_id.isoformat(),
        "as_of": week_id.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "n_sims": n_sims,
        "games_completed": len(completed),
        "games_remaining": len(remaining),
        "disclaimer": DISCLAIMER,
        "divisions": _division_payloads(standings),
        "error": None,
    }
    if write_cache:
        FUTURES_JSON.parent.mkdir(parents=True, exist_ok=True)
        FUTURES_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def refresh_nfl_futures_if_due(*, as_of: date | None = None) -> dict[str, Any]:
    """Rebuild when the Wednesday week changed or the cache is missing."""
    today = as_of or date.today()
    week_id = wednesday_week_id(today)
    season_year = current_nfl_season(today)
    saved = load_saved_nfl_futures()
    if cache_is_current(saved, as_of=week_id, season=season_year):
        return annotate_futures_payload(saved)  # type: ignore[return-value]
    return build_nfl_futures(season=season_year, as_of=today, refresh=True)
