"""See which preseason priors pick 2024–2025 conference title winners."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.cfb_priors import load_priors_store
from app.ingest.cfb_sp_plus import load_sp_plus_store
from app.models.cfb_baseline import current_elo_ratings, load_games
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_futures import (
    CONFERENCES,
    INDEPENDENT_KEY,
    assign_team_conferences,
    match_conference,
)
from app.services.cfb_playoff import regress_offseason
from app.services.cfb_preseason import pick_conference_favorite
from scripts.backtest_cfb_playoff import _games_for_season


def _title_winners(games) -> dict[str, str]:
    out = {}
    for game in games:
        if not game.get("title_game") or game.get("home_win") is None:
            continue
        spec = match_conference(str(game.get("home_conference") or ""))
        if spec is None:
            continue
        winner = game["home_team"] if int(game["home_win"]) else game["away_team"]
        out[spec["key"]] = winner
    return out


def _components(season: int, teams: list[str]) -> dict[str, dict[str, float]]:
    priors = load_priors_store((season,))
    sp = load_sp_plus_store((season,))
    hist = load_games()
    hist = hist[hist["date"] < f"{season}-08-01"]
    elo = regress_offseason(current_elo_ratings(hist)) if not hist.empty else {}
    out = {}
    for team in teams:
        t = normalize_team_name(team)
        sp_row = sp.preseason.get((season, t))
        out[t] = {
            "sp": float(sp_row.overall) if sp_row else 0.0,
            "fpi": float(priors.fpi.get((season - 1, t), 0.0)),
            "talent": float(priors.talent.get((season, t), 0.0)),
            "ret": float(priors.returning_pct.get((season, t), 0.0)),
            "ret_pass": float(priors.returning_pass_pct.get((season, t), 0.0)),
            "elo": float(elo.get(t, 1500.0)),
            "coach_chg": 1.0
            if priors.coaches.get((season, t))
            and priors.coaches.get((season - 1, t))
            and priors.coaches.get((season, t)) != priors.coaches.get((season - 1, t))
            else 0.0,
        }
    return out


def _z(values: dict[str, float]) -> dict[str, float]:
    xs = list(values.values())
    if not xs:
        return {k: 0.0 for k in values}
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / max(1, len(xs))
    sd = var**0.5 or 1.0
    return {k: (v - mean) / sd for k, v in values.items()}


WEIGHTS = {
    "sp": 0.38,
    "fpi": 0.28,
    "talent": 0.14,
    "ret": 0.10,
    "elo": 0.10,
}


def blend(comp: dict[str, dict[str, float]]) -> dict[str, float]:
    keys = ("sp", "fpi", "talent", "ret", "elo")
    zed = {k: _z({t: c[k] for t, c in comp.items()}) for k in keys}
    scores = {}
    for team, c in comp.items():
        s = sum(WEIGHTS[k] * zed[k][team] for k in keys)
        s -= 0.35 * c["coach_chg"]
        scores[team] = s
    return scores


def score_with(comp: dict[str, dict[str, float]], weights: dict[str, float], coach_pen: float) -> dict[str, float]:
    keys = [k for k in weights if k != "coach"]
    zed = {k: _z({t: c[k] for t, c in comp.items()}) for k in keys}
    scores = {}
    for team, c in comp.items():
        s = sum(weights[k] * zed[k][team] for k in keys)
        s -= coach_pen * c["coach_chg"]
        scores[team] = s
    return scores


_SEASON_CACHE: dict[int, tuple[dict, dict, dict]] = {}


def _season_pack(season: int):
    if season not in _SEASON_CACHE:
        games = _games_for_season(season)
        team_conf = assign_team_conferences(games)
        winners = _title_winners(games)
        by_conf = defaultdict(list)
        for team, conf in team_conf.items():
            if conf != INDEPENDENT_KEY:
                by_conf[conf].append(team)
        comps = {}
        for spec in CONFERENCES:
            teams = by_conf.get(spec["key"]) or []
            if len(teams) >= 4 and spec["key"] in winners:
                comps[spec["key"]] = (teams, _components(season, teams))
        _SEASON_CACHE[season] = (winners, by_conf, comps)
    return _SEASON_CACHE[season]


def evaluate_weights(weights: dict[str, float], coach_pen: float) -> tuple[int, int, list[str]]:
    hits = 0
    total = 0
    detail = []
    for season in (2024, 2025):
        winners, _by_conf, comps = _season_pack(season)
        for conf, (teams, comp) in comps.items():
            scores = score_with(comp, weights, coach_pen)
            pick = pick_conference_favorite(teams, scores, comp)
            winner = winners[conf]
            total += 1
            if pick == winner:
                hits += 1
                detail.append(f"{season} {conf} {pick}")
    return hits, total, detail


def main() -> None:
    candidates = [
        {"sp": 0.50, "fpi": 0.25, "talent": 0.15, "elo": 0.10},
        {"sp": 0.45, "fpi": 0.25, "talent": 0.20, "elo": 0.10},
        {"sp": 0.42, "fpi": 0.22, "talent": 0.18, "ret": 0.08, "elo": 0.10},
        {"sp": 0.55, "fpi": 0.20, "talent": 0.15, "elo": 0.10},
        {"sp": 0.60, "fpi": 0.20, "talent": 0.12, "elo": 0.08},
        {"sp": 0.35, "fpi": 0.30, "talent": 0.20, "elo": 0.15},
        {"sp": 0.40, "fpi": 0.20, "talent": 0.25, "elo": 0.15},
        {"sp": 0.38, "fpi": 0.28, "talent": 0.14, "ret": 0.10, "elo": 0.10},
        {"sp": 0.48, "fpi": 0.18, "talent": 0.22, "elo": 0.12},
        {"sp": 1.00},
        {"fpi": 1.00},
        {"talent": 1.00},
        {"sp": 0.70, "talent": 0.30},
        {"sp": 0.50, "talent": 0.30, "fpi": 0.20},
    ]
    best = (0, {}, 0.0, [])
    for w in candidates:
        for pen in (0.0, 0.25, 0.45):
            hits, total, detail = evaluate_weights(w, pen)
            if hits > best[0]:
                best = (hits, w, pen, detail)
    print(f"BEST {best[0]}/18 {best[1]} coach_pen={best[2]}")
    print("  hits:", ", ".join(best[3]))
    global WEIGHTS
    WEIGHTS = best[1]
    names = {c["key"]: c["name"] for c in CONFERENCES}
    hits = 0
    total = 0
    for season in (2024, 2025):
        games = _games_for_season(season)
        team_conf = assign_team_conferences(games)
        winners = _title_winners(games)
        by_conf = defaultdict(list)
        for team, conf in team_conf.items():
            if conf != INDEPENDENT_KEY:
                by_conf[conf].append(team)
        print(f"==== {season}")
        for spec in CONFERENCES:
            conf = spec["key"]
            teams = by_conf.get(conf) or []
            if len(teams) < 4 or conf not in winners:
                continue
            comp = _components(season, teams)
            scores = score_with(comp, best[1], best[2])
            ranked = sorted(teams, key=lambda t: -scores.get(t, -99))
            winner = winners[conf]
            rank = ranked.index(winner) + 1 if winner in ranked else None
            pick = pick_conference_favorite(teams, scores, comp)
            hit = pick == winner
            hits += int(hit)
            total += 1
            print(
                f"  {names[conf]:<22} pick {pick:<18} won {winner:<18} "
                f"rank {rank}  {'HIT' if hit else 'miss'}"
            )
            # show top 3 scores
            for t in ranked[:3]:
                c = comp[t]
                print(
                    f"     {t:<18} blend {scores[t]:+.2f}  SP {c['sp']:+5.1f}  "
                    f"FPI {c['fpi']:+5.1f}  tal {c['talent']:5.1f}  ret {c['ret']:.2f}  "
                    f"elo {c['elo']:.0f}"
                )
    print(f"\nTitle-winner hits: {hits}/{total} ({hits / total:.0%})")


if __name__ == "__main__":
    main()
