"""CFB conference placement + 12-team playoff futures.

Sunday snapshot: lock results through Saturday, project the rest of the
regular season with the active moneyline model, then rank every FBS
conference 1-through-last and build a 12-team CFP field.
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
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_playoff import regress_offseason, select_playoff_indices
from app.services.cfb_slate_predictions import cfb_season_end_year
from app.services.cfb_team_logos import lookup_team_logo

logger = logging.getLogger(__name__)

FUTURES_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_futures.json"
N_SIMS = 5000
TITLE_GAME_MIN_WEEK = 13
AUTO_BIDS = 5
PLAYOFF_FIELD = 12
BYE_SEEDS = 4

CONFERENCES: tuple[dict[str, Any], ...] = (
    {
        "key": "sec",
        "name": "SEC",
        "tier": "power",
        "aliases": ("sec", "southeastern"),
    },
    {
        "key": "big_ten",
        "name": "Big Ten",
        "tier": "power",
        "aliases": ("big ten", "big 10"),
    },
    {
        "key": "big_12",
        "name": "Big 12",
        "tier": "power",
        "aliases": ("big 12", "big twelve"),
    },
    {
        "key": "acc",
        "name": "ACC",
        "tier": "power",
        "aliases": ("acc", "atlantic coast"),
    },
    {
        "key": "pac_12",
        "name": "Pac-12",
        "tier": "group",
        "aliases": ("pac-12", "pac 12", "pac12"),
    },
    {
        "key": "aac",
        "name": "American Athletic",
        "tier": "group",
        "aliases": ("american athletic", "aac"),
    },
    {
        "key": "mwc",
        "name": "Mountain West",
        "tier": "group",
        "aliases": ("mountain west", "mwc"),
    },
    {
        "key": "sun_belt",
        "name": "Sun Belt",
        "tier": "group",
        "aliases": ("sun belt",),
    },
    {
        "key": "mac",
        "name": "MAC",
        "tier": "group",
        "aliases": ("mid-american", "mac"),
    },
    {
        "key": "cusa",
        "name": "Conference USA",
        "tier": "group",
        "aliases": ("conference usa", "cusa", "c-usa"),
    },
)


def sunday_week_id(as_of: date) -> date:
    """Most recent Sunday (today if Sunday). Futures reset on this day."""
    days_since_sunday = (as_of.weekday() + 1) % 7
    return as_of - timedelta(days=days_since_sunday)


def current_cfb_season(as_of: date | None = None) -> int:
    return cfb_season_end_year(as_of or date.today())


def match_conference(name: str | None) -> dict[str, Any] | None:
    raw = str(name or "").strip().lower()
    if not raw or "independent" in raw:
        return None
    hits: list[tuple[int, dict[str, Any]]] = []
    for spec in CONFERENCES:
        for alias in spec["aliases"]:
            if raw == alias or raw.startswith(f"{alias} ") or raw.endswith(f" {alias}"):
                hits.append((len(alias), spec))
                break
            if alias in raw.split():
                hits.append((len(alias), spec))
                break
    if not hits:
        return None
    hits.sort(key=lambda item: item[0], reverse=True)
    return hits[0][1]


def _team_logo(team: str) -> str | None:
    meta = lookup_team_logo(team)
    if not meta:
        return None
    url = meta.get("logo_url")
    return str(url) if url else None


INDEPENDENT_KEY = "independent"


def assign_team_conferences(games: list[dict[str, Any]]) -> dict[str, str]:
    """Map team → conference key. Independents are included for the playoff pool."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for game in games:
        for side in ("home", "away"):
            team = normalize_team_name(str(game.get(f"{side}_team") or ""))
            if not team:
                continue
            label = str(game.get(f"{side}_conference") or "")
            spec = match_conference(label)
            if spec is not None:
                counts[team][spec["key"]] += 1
            elif "independent" in label.lower():
                counts[team][INDEPENDENT_KEY] += 1
    assigned: dict[str, str] = {}
    for team, conf_counts in counts.items():
        assigned[team] = max(conf_counts.items(), key=lambda item: item[1])[0]
    return assigned


def mark_title_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag conference championships.

    CFBD marks most title games as conferenceGame, but 2025 left that flag
    off. Week 15 same-conference FBS games are title games. Army–Navy (week
    16) is not.
    """
    by_conf_week: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        game["title_game"] = False
        home_spec = match_conference(str(game.get("home_conference") or ""))
        away_spec = match_conference(str(game.get("away_conference") or ""))
        if home_spec is None or away_spec is None or home_spec["key"] != away_spec["key"]:
            continue
        week = int(game.get("week") or 0)
        same = home_spec["key"]
        if week == 15:
            game["title_game"] = True
            continue
        if not int(game.get("conference_game") or 0):
            continue
        if week >= TITLE_GAME_MIN_WEEK:
            by_conf_week[(same, week)].append(game)

    for (conf_key, week), rows in by_conf_week.items():
        del conf_key
        if week == 16:
            continue
        if week >= TITLE_GAME_MIN_WEEK and len(rows) == 1:
            rows[0]["title_game"] = True
    return games


def split_completed_remaining(
    games: list[dict[str, Any]],
    *,
    as_of: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Games dated before this Sunday with a winner are locked."""
    completed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for game in games:
        game_day = date.fromisoformat(str(game["date"])[:10])
        has_winner = game.get("home_win") is not None
        if game_day < as_of and has_winner:
            completed.append(game)
        else:
            remaining.append(game)
    return completed, remaining


def actual_records(
    completed: list[dict[str, Any]],
    team_conf: dict[str, str],
) -> dict[str, dict[str, int]]:
    records: dict[str, dict[str, int]] = defaultdict(
        lambda: {"conf_wins": 0, "conf_losses": 0, "wins": 0, "losses": 0}
    )
    for game in completed:
        home = normalize_team_name(str(game["home_team"]))
        away = normalize_team_name(str(game["away_team"]))
        home_win = int(game["home_win"])
        winner, loser = (home, away) if home_win else (away, home)
        records[winner]["wins"] += 1
        records[loser]["losses"] += 1
        if int(game.get("conference_game") or 0) and not game.get("title_game"):
            if team_conf.get(home) and team_conf.get(home) == team_conf.get(away):
                records[winner]["conf_wins"] += 1
                records[loser]["conf_losses"] += 1
    return records


def elo_win_prob(home_elo: float, away_elo: float, *, neutral: bool = False) -> float:
    adv = 0.0 if neutral else 55.0
    return 1.0 / (1.0 + 10 ** ((away_elo - home_elo - adv) / 400.0))


def _clip_prob(value: float) -> float:
    return float(min(0.97, max(0.03, value)))


def _simulate_strength_game(
    rng: np.random.Generator,
    first: int,
    second: int,
    strength: np.ndarray,
    *,
    neutral: bool,
) -> int:
    """Draw one matchup from the same strength scale used by futures."""
    p_first = _clip_prob(
        elo_win_prob(
            float(strength[first]),
            float(strength[second]),
            neutral=neutral,
        )
    )
    return first if rng.random() < p_first else second


def _simulate_playoff_field(
    field: list[int],
    strength: np.ndarray,
    rng: np.random.Generator,
) -> dict[str, Any] | None:
    """Simulate the official fixed 12-team bracket.

    First-round games use the higher seed's home field. Every later round is
    neutral. The returned stage lists contain the teams that reached that
    round, so callers can aggregate advancement probabilities.
    """
    if len(field) < PLAYOFF_FIELD:
        return None

    by_seed = {seed: field[seed - 1] for seed in range(1, PLAYOFF_FIELD + 1)}
    first_round_winners = {
        high: _simulate_strength_game(
            rng,
            by_seed[high],
            by_seed[low],
            strength,
            neutral=False,
        )
        for high, low in ((5, 12), (6, 11), (7, 10), (8, 9))
    }
    quarterfinalists = [
        by_seed[1],
        by_seed[4],
        by_seed[2],
        by_seed[3],
        first_round_winners[8],
        first_round_winners[5],
        first_round_winners[7],
        first_round_winners[6],
    ]
    quarterfinal_winners = [
        _simulate_strength_game(
            rng, by_seed[1], first_round_winners[8], strength, neutral=True
        ),
        _simulate_strength_game(
            rng, by_seed[4], first_round_winners[5], strength, neutral=True
        ),
        _simulate_strength_game(
            rng, by_seed[2], first_round_winners[7], strength, neutral=True
        ),
        _simulate_strength_game(
            rng, by_seed[3], first_round_winners[6], strength, neutral=True
        ),
    ]
    semifinalists = quarterfinal_winners
    finalists = [
        _simulate_strength_game(
            rng,
            quarterfinal_winners[0],
            quarterfinal_winners[1],
            strength,
            neutral=True,
        ),
        _simulate_strength_game(
            rng,
            quarterfinal_winners[2],
            quarterfinal_winners[3],
            strength,
            neutral=True,
        ),
    ]
    champion = _simulate_strength_game(
        rng,
        finalists[0],
        finalists[1],
        strength,
        neutral=True,
    )
    return {
        "byes": field[:BYE_SEEDS],
        "quarterfinalists": quarterfinalists,
        "semifinalists": semifinalists,
        "finalists": finalists,
        "champion": champion,
    }


def project_from_probs(
    *,
    team_conf: dict[str, str],
    records: dict[str, dict[str, int]],
    remaining: list[dict[str, Any]],
    probs: dict[str, float],
    strength: dict[str, float],
    n_sims: int = N_SIMS,
    seed: int = 0,
    season: int = 2026,
    season_progress: float = 0.0,
) -> dict[str, Any]:
    """Project records, conference races, CFP selection, and the full bracket."""
    teams = sorted(team_conf)
    team_index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)
    rng = np.random.default_rng(seed)

    conf_wins = np.zeros((n_sims, n_teams), dtype=np.float64)
    overall_wins = np.zeros((n_sims, n_teams), dtype=np.float64)
    games_scheduled = np.zeros(n_teams, dtype=np.int32)
    for team, rec in records.items():
        idx = team_index.get(team)
        if idx is None:
            continue
        conf_wins[:, idx] = rec["conf_wins"]
        overall_wins[:, idx] = rec["wins"]
        games_scheduled[idx] = int(rec["wins"]) + int(rec["losses"])

    for game in remaining:
        gid = str(game["game_id"])
        p_home = _clip_prob(float(probs.get(gid, 0.5)))
        home = normalize_team_name(str(game["home_team"]))
        away = normalize_team_name(str(game["away_team"]))
        if home not in team_index or away not in team_index:
            continue
        if game.get("title_game"):
            continue
        hi = team_index[home]
        ai = team_index[away]
        games_scheduled[hi] += 1
        games_scheduled[ai] += 1
        draws = rng.random(n_sims) < p_home
        overall_wins[draws, hi] += 1
        overall_wins[~draws, ai] += 1
        same_conf = (
            int(game.get("conference_game") or 0)
            and team_conf.get(home) == team_conf.get(away)
        )
        if same_conf:
            conf_wins[draws, hi] += 1
            conf_wins[~draws, ai] += 1

    conf_keys = sorted(
        {key for key in team_conf.values() if key != INDEPENDENT_KEY}
    )
    teams_by_conf: dict[str, list[int]] = defaultdict(list)
    for team, conf_key in team_conf.items():
        teams_by_conf[conf_key].append(team_index[team])

    max_conf_size = max((len(v) for v in teams_by_conf.values()), default=1)
    champ_counts = np.zeros(n_teams, dtype=np.int32)
    title_game_counts = np.zeros(n_teams, dtype=np.int32)
    finish_sum = np.zeros(n_teams, dtype=np.float64)
    finish_counts = np.zeros((n_teams, max_conf_size), dtype=np.int32)
    playoff_counts = np.zeros(n_teams, dtype=np.int32)
    bye_counts = np.zeros(n_teams, dtype=np.int32)
    quarterfinal_counts = np.zeros(n_teams, dtype=np.int32)
    semifinal_counts = np.zeros(n_teams, dtype=np.int32)
    finalist_counts = np.zeros(n_teams, dtype=np.int32)
    national_title_counts = np.zeros(n_teams, dtype=np.int32)
    seed_sum = np.zeros(n_teams, dtype=np.float64)

    strength_arr = np.array(
        [float(strength.get(team, 1500.0)) for team in teams],
        dtype=np.float64,
    )
    sos_arr, qwin_arr = _resume_arrays(
        teams, team_index, records, remaining, probs, strength
    )

    for sim in range(n_sims):
        champs: dict[str, int] = {}
        for conf_key in conf_keys:
            idxs = teams_by_conf[conf_key]
            order = sorted(
                idxs,
                key=lambda i: (
                    -conf_wins[sim, i],
                    -overall_wins[sim, i],
                    -strength_arr[i],
                    teams[i],
                ),
            )
            for place, idx in enumerate(order, start=1):
                finish_sum[idx] += place
                finish_counts[idx, place - 1] += 1
            if len(order) >= 2:
                top, second = order[0], order[1]
                title_game_counts[top] += 1
                title_game_counts[second] += 1
                champ = _simulate_strength_game(
                    rng, top, second, strength_arr, neutral=True
                )
            else:
                champ = order[0]
                title_game_counts[champ] += 1
            champs[conf_key] = champ
            champ_counts[champ] += 1

        field, _auto = _select_playoff_indices(
            champs=champs,
            overall=overall_wins[sim],
            strength=strength_arr,
            teams=teams,
            team_conf=team_conf,
            sos=sos_arr,
            quality_wins=qwin_arr,
            season=season,
            season_progress=season_progress,
        )
        for seed_num, idx in enumerate(field, start=1):
            playoff_counts[idx] += 1
            seed_sum[idx] += seed_num

        bracket = _simulate_playoff_field(field, strength_arr, rng)
        if bracket is None:
            continue
        for idx in bracket["byes"]:
            bye_counts[idx] += 1
        for idx in bracket["quarterfinalists"]:
            quarterfinal_counts[idx] += 1
        for idx in bracket["semifinalists"]:
            semifinal_counts[idx] += 1
        for idx in bracket["finalists"]:
            finalist_counts[idx] += 1
        national_title_counts[bracket["champion"]] += 1

    expected_conf = conf_wins.mean(axis=0)
    expected_overall = overall_wins.mean(axis=0)
    mean_finish = finish_sum / n_sims
    title_game_pct = title_game_counts / n_sims
    title_pct = champ_counts / n_sims
    playoff_pct = playoff_counts / n_sims
    bye_pct = bye_counts / n_sims
    quarterfinal_pct = quarterfinal_counts / n_sims
    semifinal_pct = semifinal_counts / n_sims
    finalist_pct = finalist_counts / n_sims
    national_title_pct = national_title_counts / n_sims
    mean_seed = np.divide(
        seed_sum,
        np.maximum(playoff_counts, 1),
        dtype=np.float64,
    )

    def _projection_row(idx: int) -> dict[str, Any]:
        team = teams[idx]
        rec = records.get(
            team,
            {"conf_wins": 0, "conf_losses": 0, "wins": 0, "losses": 0},
        )
        win_samples = overall_wins[:, idx].astype(np.int32)
        counts = np.bincount(win_samples)
        likely_wins = int(np.argmax(counts)) if counts.size else 0
        ordered = np.sort(win_samples)
        low_idx = int(round((len(ordered) - 1) * 0.10)) if len(ordered) else 0
        high_idx = int(round((len(ordered) - 1) * 0.90)) if len(ordered) else 0
        win_low = int(ordered[low_idx]) if len(ordered) else 0
        win_high = int(ordered[high_idx]) if len(ordered) else 0
        scheduled = int(games_scheduled[idx])
        expected_wins = float(expected_overall[idx])
        distribution = [
            {"wins": int(wins), "pct": round(float(count / n_sims), 4)}
            for wins, count in enumerate(counts)
            if count
        ]
        return {
            "team": team,
            "logo_url": _team_logo(team),
            "conference_key": team_conf.get(team, ""),
            "actual_conf_wins": int(rec["conf_wins"]),
            "actual_conf_losses": int(rec["conf_losses"]),
            "actual_wins": int(rec["wins"]),
            "actual_losses": int(rec["losses"]),
            "games_scheduled": scheduled,
            "expected_conf_wins": round(float(expected_conf[idx]), 2),
            "expected_wins": round(expected_wins, 2),
            "expected_losses": round(max(0.0, scheduled - expected_wins), 2),
            "likely_wins": likely_wins,
            "likely_losses": max(0, scheduled - likely_wins),
            "likely_record": f"{likely_wins}-{max(0, scheduled - likely_wins)}",
            "win_range_low": win_low,
            "win_range_high": win_high,
            "win_distribution": distribution,
            "bowl_pct": round(float(np.mean(win_samples >= 6)), 4),
            "nine_win_pct": round(float(np.mean(win_samples >= 9)), 4),
            "ten_win_pct": round(float(np.mean(win_samples >= 10)), 4),
            "eleven_win_pct": round(float(np.mean(win_samples >= 11)), 4),
            "undefeated_pct": round(
                float(np.mean(win_samples >= scheduled)) if scheduled else 0.0,
                4,
            ),
            "mean_finish": round(float(mean_finish[idx]), 2),
            "title_game_pct": round(float(title_game_pct[idx]), 4),
            "title_pct": round(float(title_pct[idx]), 4),
            "playoff_pct": round(float(playoff_pct[idx]), 4),
            "bye_pct": round(float(bye_pct[idx]), 4),
            "quarterfinal_pct": round(float(quarterfinal_pct[idx]), 4),
            "semifinal_pct": round(float(semifinal_pct[idx]), 4),
            "final_pct": round(float(finalist_pct[idx]), 4),
            "national_title_pct": round(float(national_title_pct[idx]), 4),
            "mean_seed": (
                round(float(mean_seed[idx]), 2) if playoff_counts[idx] > 0 else None
            ),
            "strength": round(float(strength_arr[idx]), 1),
        }

    projections = {team: _projection_row(idx) for idx, team in enumerate(teams)}

    standings_by_conf: dict[str, list[dict[str, Any]]] = {}
    for spec in CONFERENCES:
        conf_key = spec["key"]
        idxs = teams_by_conf.get(conf_key) or []
        if not idxs:
            continue
        if season_progress < 0.25:
            ranked = sorted(
                idxs,
                key=lambda i: (
                    -title_pct[i],
                    mean_finish[i],
                    -expected_conf[i],
                    -strength_arr[i],
                    teams[i],
                ),
            )
        else:
            ranked = sorted(
                idxs,
                key=lambda i: (
                    mean_finish[i],
                    -title_pct[i],
                    -expected_conf[i],
                    -strength_arr[i],
                    teams[i],
                ),
            )
        rows = []
        for place, idx in enumerate(ranked, start=1):
            row = dict(projections[teams[idx]])
            row["place"] = place
            rows.append(row)
        standings_by_conf[conf_key] = rows

    overall_ranked = sorted(
        range(n_teams),
        key=lambda i: (
            -national_title_pct[i],
            -playoff_pct[i],
            -expected_overall[i],
            -strength_arr[i],
            teams[i],
        ),
    )
    overall = []
    for rank, idx in enumerate(overall_ranked, start=1):
        row = dict(projections[teams[idx]])
        row["rank"] = rank
        overall.append(row)

    published_champs: dict[str, int] = {}
    for spec in CONFERENCES:
        rows = standings_by_conf.get(spec["key"]) or []
        if rows:
            published_champs[spec["key"]] = team_index[rows[0]["team"]]

    published_field, published_auto = _select_playoff_indices(
        champs=published_champs,
        overall=expected_overall,
        strength=strength_arr,
        teams=teams,
        team_conf=team_conf,
        sos=sos_arr,
        quality_wins=qwin_arr,
        season=season,
        season_progress=season_progress,
    )
    playoff = _playoff_payload(
        field=published_field,
        auto=published_auto,
        teams=teams,
        team_conf=team_conf,
        standings_by_conf=standings_by_conf,
        projections=projections,
        strength=strength_arr,
        overall=overall,
    )
    return {
        "conferences": standings_by_conf,
        "overall": overall,
        "playoff": playoff,
    }


def _resume_arrays(
    teams: list[str],
    team_index: dict[str, int],
    records: dict[str, dict[str, int]],
    remaining: list[dict[str, Any]],
    probs: dict[str, float],
    strength: dict[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    sos_sum = np.zeros(len(teams), dtype=np.float64)
    sos_n = np.zeros(len(teams), dtype=np.float64)
    qwins = np.zeros(len(teams), dtype=np.float64)
    for game in remaining:
        home = normalize_team_name(str(game["home_team"]))
        away = normalize_team_name(str(game["away_team"]))
        if home not in team_index or away not in team_index:
            continue
        hi, ai = team_index[home], team_index[away]
        hs, aws = strength.get(home, 1500.0), strength.get(away, 1500.0)
        p_home = _clip_prob(float(probs.get(str(game["game_id"]), 0.5)))
        sos_sum[hi] += aws
        sos_sum[ai] += hs
        sos_n[hi] += 1
        sos_n[ai] += 1
        if aws >= 1600:
            qwins[hi] += p_home
        if hs >= 1600:
            qwins[ai] += 1.0 - p_home
    sos = np.where(sos_n > 0, sos_sum / np.maximum(sos_n, 1.0), 1500.0)
    return sos, qwins


def _select_playoff_indices(
    *,
    champs: dict[str, int],
    overall: np.ndarray,
    strength: np.ndarray,
    teams: list[str],
    team_conf: dict[str, str] | None = None,
    sos: np.ndarray | None = None,
    quality_wins: np.ndarray | None = None,
    season: int = 2026,
    season_progress: float = 1.0,
) -> tuple[list[int], set[int]]:
    return select_playoff_indices(
        champs=champs,
        wins=overall,
        strength=strength,
        teams=teams,
        team_conf=team_conf or {},
        sos=sos,
        quality_wins=quality_wins,
        season=season,
        season_progress=season_progress,
    )


def _playoff_payload(
    *,
    field: list[int],
    auto: set[int],
    teams: list[str],
    team_conf: dict[str, str],
    standings_by_conf: dict[str, list[dict[str, Any]]],
    projections: dict[str, dict[str, Any]],
    strength: np.ndarray,
    overall: list[dict[str, Any]],
) -> dict[str, Any]:
    conf_name = {spec["key"]: spec["name"] for spec in CONFERENCES}
    champ_teams = {
        rows[0]["team"]: spec_key
        for spec_key, rows in standings_by_conf.items()
        if rows
    }
    seeds = []
    for seed_num, idx in enumerate(field, start=1):
        team = teams[idx]
        conf_key = team_conf.get(team, "")
        display_conf = (
            "Independent"
            if conf_key == INDEPENDENT_KEY
            else conf_name.get(conf_key, conf_key)
        )
        projection = projections[team]
        seeds.append(
            {
                "seed": seed_num,
                "team": team,
                "logo_url": projection["logo_url"],
                "conference_key": conf_key,
                "conference": display_conf,
                "auto_bid": idx in auto,
                "conference_champ": team in champ_teams,
                "bye": seed_num <= BYE_SEEDS,
                "expected_wins": projection["expected_wins"],
                "likely_record": projection["likely_record"],
                "playoff_pct": projection["playoff_pct"],
                "bye_pct": projection["bye_pct"],
                "semifinal_pct": projection["semifinal_pct"],
                "final_pct": projection["final_pct"],
                "national_title_pct": projection["national_title_pct"],
                "mean_seed": projection["mean_seed"],
            }
        )
    first_round = []
    pairings = ((5, 12), (6, 11), (7, 10), (8, 9))
    by_seed = {row["seed"]: row for row in seeds}
    index_by_team = {team: idx for idx, team in enumerate(teams)}
    for high, low in pairings:
        if high not in by_seed or low not in by_seed:
            continue
        home = by_seed[high]
        away = by_seed[low]
        home_idx = index_by_team[home["team"]]
        away_idx = index_by_team[away["team"]]
        home_win_pct = _clip_prob(
            elo_win_prob(
                float(strength[home_idx]),
                float(strength[away_idx]),
                neutral=False,
            )
        )
        first_round.append(
            {
                "home": home,
                "away": away,
                "home_win_pct": round(float(home_win_pct), 4),
                "away_win_pct": round(float(1.0 - home_win_pct), 4),
            }
        )

    odds = []
    for row in overall:
        conf_key = str(row.get("conference_key") or "")
        odds.append(
            {
                "rank": row["rank"],
                "team": row["team"],
                "logo_url": row["logo_url"],
                "conference_key": conf_key,
                "conference": (
                    "Independent"
                    if conf_key == INDEPENDENT_KEY
                    else conf_name.get(conf_key, conf_key)
                ),
                "likely_record": row["likely_record"],
                "playoff_pct": row["playoff_pct"],
                "bye_pct": row["bye_pct"],
                "quarterfinal_pct": row["quarterfinal_pct"],
                "semifinal_pct": row["semifinal_pct"],
                "final_pct": row["final_pct"],
                "national_title_pct": row["national_title_pct"],
                "mean_seed": row["mean_seed"],
            }
        )
    return {
        "format": "12-team CFP",
        "rules": (
            "ACC, Big Ten, Big 12 and SEC champions; highest-ranked Group of 6 "
            "team; seven at-large teams. The four highest-ranked teams receive byes."
        ),
        "auto_bids": AUTO_BIDS,
        "field_size": PLAYOFF_FIELD,
        "postseason_model": "model_strength_matchup",
        "seeds": seeds,
        "first_round": first_round,
        "odds": odds,
    }


def _slate_frame(games: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for game in games:
        rows.append(
            {
                "game_id": str(game["game_id"]),
                "date": game["date"],
                "season": int(game["season"]),
                "week": int(game.get("week") or 0),
                "home_team": normalize_team_name(str(game["home_team"])),
                "away_team": normalize_team_name(str(game["away_team"])),
                "home_conference": game.get("home_conference") or "",
                "away_conference": game.get("away_conference") or "",
                "conference_game": int(game.get("conference_game") or 0),
                "neutral_site": int(game.get("neutral_site") or 0),
            }
        )
    return pd.DataFrame(rows)


def _history_frame(completed: list[dict[str, Any]]) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    hist = load_games()
    extra_rows = []
    for game in completed:
        extra_rows.append(
            {
                "game_id": str(game["game_id"]),
                "date": game["date"],
                "season": int(game["season"]),
                "game_type": "regular",
                "home_team": normalize_team_name(str(game["home_team"])),
                "away_team": normalize_team_name(str(game["away_team"])),
                "home_score": game.get("home_score"),
                "away_score": game.get("away_score"),
                "home_win": int(game["home_win"]),
                "neutral_site": int(game.get("neutral_site") or 0),
                "conference_game": int(game.get("conference_game") or 0),
                "home_conference": game.get("home_conference") or "",
                "away_conference": game.get("away_conference") or "",
                "week": int(game.get("week") or 0),
            }
        )
    if extra_rows:
        extra = pd.DataFrame(extra_rows)
        extra["date"] = pd.to_datetime(extra["date"])
        hist = pd.concat([hist, extra], ignore_index=True, sort=False)
        hist = hist.drop_duplicates(subset=["game_id"], keep="last")
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.sort_values(["date", "game_id"]).reset_index(drop=True)
    return hist


def predict_remaining_probs(
    remaining: list[dict[str, Any]],
    completed: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, float], str]:
    """v4 moneyline probs for remaining games; Elo fallback if the model fails."""
    from app.models.cfb_baseline import current_elo_ratings, predict_home_win_proba

    hist = _history_frame(completed)
    ratings = current_elo_ratings(hist[hist["home_win"].notna()]) if not hist.empty else {}
    strength = {normalize_team_name(str(k)): float(v) for k, v in ratings.items()}
    elo_probs: dict[str, float] = {}
    for game in remaining:
        home = normalize_team_name(str(game["home_team"]))
        away = normalize_team_name(str(game["away_team"]))
        elo_probs[str(game["game_id"])] = elo_win_prob(
            strength.get(home, 1500.0),
            strength.get(away, 1500.0),
            neutral=bool(game.get("neutral_site")),
        )
        strength.setdefault(home, 1500.0)
        strength.setdefault(away, 1500.0)

    if not remaining:
        return {}, strength, "none"

    try:
        slate = _slate_frame(remaining)
        raw = predict_home_win_proba(slate)
        model_probs = {
            str(gid): _clip_prob(float(p))
            for gid, p in zip(slate["game_id"].astype(str), raw)
        }
        return model_probs, strength, "v4_logistic_platt"
    except Exception as exc:
        logger.warning("CFB futures model probs failed, using Elo: %s", exc)
        return elo_probs, strength, "elo_fallback"


def load_saved_cfb_futures() -> dict[str, Any] | None:
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
        and bool(payload.get("conferences"))
    )


def _empty_payload(
    *,
    season: int,
    as_of: date,
    error: str,
) -> dict[str, Any]:
    return {
        "sport": "cfb",
        "season": season,
        "week_id": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": None,
        "n_sims": N_SIMS,
        "games_completed": 0,
        "games_remaining": 0,
        "disclaimer": (
            "Sunday snapshot: projected records, conference races, CFP selection, "
            "and playoff advancement. Resets each Sunday after Saturday results."
        ),
        "conferences": [],
        "overall": [],
        "playoff": {
            "format": "12-team CFP",
            "auto_bids": AUTO_BIDS,
            "field_size": PLAYOFF_FIELD,
            "postseason_model": "model_strength_matchup",
            "seeds": [],
            "first_round": [],
            "odds": [],
        },
        "error": error,
    }


def _conference_payloads(standings_by_conf: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out = []
    for spec in CONFERENCES:
        rows = standings_by_conf.get(spec["key"]) or []
        if not rows:
            continue
        out.append(
            {
                "key": spec["key"],
                "name": spec["name"],
                "tier": spec["tier"],
                "champion": rows[0]["team"],
                "teams": rows,
            }
        )
    return out


def build_cfb_futures(
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
    week_id = sunday_week_id(today)
    season_year = int(season or current_cfb_season(today))

    if not refresh:
        saved = load_saved_cfb_futures()
        if cache_is_current(saved, as_of=week_id, season=season_year):
            return saved  # type: ignore[return-value]

    if games is None:
        from app.ingest.cfb_season_schedule import ensure_season_schedule

        try:
            from app.ingest.cfb_priors import ensure_priors_cache
            from app.ingest.cfb_sp_plus import ensure_sp_plus_cache

            ensure_priors_cache((season_year,))
            ensure_sp_plus_cache((season_year,))
        except Exception as exc:
            logger.warning("CFB futures cache warm skipped: %s", exc)
        try:
            games = ensure_season_schedule(season_year, force=refresh)
        except (Exception, SystemExit) as exc:
            logger.warning("CFB season schedule fetch failed: %s", exc)
            payload = _empty_payload(
                season=season_year,
                as_of=week_id,
                error=f"Could not load {season_year} FBS schedule: {exc}",
            )
            if write_cache:
                FUTURES_JSON.parent.mkdir(parents=True, exist_ok=True)
                FUTURES_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload

    if not games:
        return _empty_payload(
            season=season_year,
            as_of=week_id,
            error=f"No {season_year} FBS schedule games yet.",
        )

    games = mark_title_games([dict(g) for g in games])
    team_conf = assign_team_conferences(games)
    completed, remaining = split_completed_remaining(games, as_of=week_id)
    records = actual_records(completed, team_conf)

    injected = probs is not None and strength is not None
    model_name = "injected"
    if not injected:
        probs, strength, model_name = predict_remaining_probs(remaining, completed)
    assert probs is not None
    assert strength is not None
    done_weeks = [int(g.get("week") or 0) for g in completed]
    through_week = max(done_weeks) if done_weeks else 0
    season_progress = min(1.0, through_week / 12.0)
    if not injected:
        from app.services.cfb_preseason import mix_preseason_strength, rebuild_probs_from_strength

        if season_progress < 0.45:
            strength = regress_offseason(strength)
        strength = mix_preseason_strength(
            season=season_year,
            team_conf=team_conf,
            live=strength,
            through_week=through_week,
        )
        if through_week < 3:
            probs = rebuild_probs_from_strength(remaining, strength, win_prob=elo_win_prob)
            model_name = "preseason_prior_blend"

    seed = int(week_id.strftime("%Y%m%d"))
    projected = project_from_probs(
        team_conf=team_conf,
        records=records,
        remaining=remaining,
        probs=probs,
        strength=strength,
        n_sims=n_sims,
        seed=seed,
        season=season_year,
        season_progress=season_progress,
    )
    payload = {
        "sport": "cfb",
        "season": season_year,
        "week_id": week_id.isoformat(),
        "as_of": week_id.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "n_sims": n_sims,
        "games_completed": len(completed),
        "games_remaining": len(remaining),
        "disclaimer": (
            "Sunday snapshot from the corrected CFB game model. Scheduled games "
            "drive projected records and conference races; neutral model-strength "
            "matchups drive conference title games and the CFP bracket. 2026 CFP "
            "rules: Power 4 champions, the highest-ranked Group of 6 team, seven "
            "at-large teams, and byes for the four highest-ranked teams."
        ),
        "conferences": _conference_payloads(projected["conferences"]),
        "overall": projected["overall"],
        "playoff": projected["playoff"],
        "error": None,
    }
    if write_cache:
        FUTURES_JSON.parent.mkdir(parents=True, exist_ok=True)
        FUTURES_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def refresh_cfb_futures_if_due(*, as_of: date | None = None) -> dict[str, Any]:
    """Rebuild when the Sunday week changed or the cache is missing."""
    today = as_of or date.today()
    week_id = sunday_week_id(today)
    season_year = current_cfb_season(today)
    saved = load_saved_cfb_futures()
    if cache_is_current(saved, as_of=week_id, season=season_year):
        return saved  # type: ignore[return-value]
    return build_cfb_futures(season=season_year, as_of=today, refresh=True)
