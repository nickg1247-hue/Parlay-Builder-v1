"""Walk 2024–2025 CFP fields: end-of-season and in-season Sunday snapshots."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.cfb_season_schedule import ensure_season_schedule
from app.models.cfb_baseline import current_elo_ratings, load_games
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_futures import (
    assign_team_conferences,
    elo_win_prob,
    mark_title_games,
    match_conference,
)
from app.services.cfb_playoff import (
    ACTUAL_FIELDS,
    field_hit_rate,
    misses,
    regress_offseason,
    select_playoff_indices,
)


def _games_for_season(season: int) -> list[dict]:
    """Prefer CFBD season schedule (has week + conference); fall back to parquet."""
    try:
        rows = ensure_season_schedule(season, force=False)
    except Exception:
        rows = []
    games = []
    for row in rows:
        if not row.get("completed") or row.get("home_win") is None:
            continue
        games.append(dict(row))
    if games:
        return mark_title_games(games)
    df = load_games()
    sub = df[df["season"] == season].copy()
    for row in sub.itertuples(index=False):
        games.append(
            {
                "game_id": str(row.game_id),
                "date": str(row.date)[:10],
                "season": int(row.season),
                "week": int(getattr(row, "week", 0) or 0),
                "home_team": normalize_team_name(str(row.home_team)),
                "away_team": normalize_team_name(str(row.away_team)),
                "home_conference": str(getattr(row, "home_conference", "") or ""),
                "away_conference": str(getattr(row, "away_conference", "") or ""),
                "conference_game": int(getattr(row, "conference_game", 0) or 0),
                "neutral_site": int(getattr(row, "neutral_site", 0) or 0),
                "completed": True,
                "home_win": int(row.home_win),
                "home_score": getattr(row, "home_score", None),
                "away_score": getattr(row, "away_score", None),
            }
        )
    return mark_title_games(games)


def _records_and_champs(games: list[dict], team_conf: dict[str, str]):
    rec = defaultdict(lambda: {"wins": 0, "losses": 0, "conf_wins": 0, "conf_losses": 0})
    qwins = defaultdict(float)
    sos_sum = defaultdict(float)
    sos_n = defaultdict(int)
    for game in games:
        if game.get("home_win") is None:
            continue
        home = game["home_team"]
        away = game["away_team"]
        hw = int(game["home_win"])
        winner, loser = (home, away) if hw else (away, home)
        rec[winner]["wins"] += 1
        rec[loser]["losses"] += 1
        if int(game.get("conference_game") or 0) and not game.get("title_game"):
            if team_conf.get(home) == team_conf.get(away) and team_conf.get(home):
                rec[winner]["conf_wins"] += 1
                rec[loser]["conf_losses"] += 1
    return rec, qwins, sos_sum, sos_n


def _detect_champs(rec, team_conf, strength, games=None) -> dict[str, str]:
    title_winner: dict[str, str] = {}
    for game in games or []:
        if not game.get("title_game") or game.get("home_win") is None:
            continue
        spec = match_conference(str(game.get("home_conference") or ""))
        if spec is None:
            continue
        winner = game["home_team"] if int(game["home_win"]) else game["away_team"]
        title_winner[spec["key"]] = winner
    by_conf = defaultdict(list)
    for team, conf in team_conf.items():
        if conf == "independent":
            continue
        by_conf[conf].append(team)
    champs = {}
    for conf, teams in by_conf.items():
        teams.sort(
            key=lambda t: (
                -rec.get(t, {}).get("conf_wins", 0),
                -rec.get(t, {}).get("wins", 0),
                -float(strength.get(t, 1500.0)),
                t,
            )
        )
        if conf in title_winner:
            champs[conf] = title_winner[conf]
        elif teams:
            champs[conf] = teams[0]
    return champs


def _select(season, teams, team_conf, rec, strength, sos, qwins, champs, progress=1.0):
    team_index = {t: i for i, t in enumerate(teams)}
    champ_idx = {c: team_index[t] for c, t in champs.items() if t in team_index}
    wins = [float(rec.get(t, {}).get("wins", 0)) for t in teams]
    strn = [float(strength.get(t, 1500.0)) for t in teams]
    sos_l = [float(sos.get(t, 1500.0)) for t in teams]
    qw = [float(qwins.get(t, 0.0)) for t in teams]
    field, auto = select_playoff_indices(
        champs=champ_idx,
        wins=wins,
        strength=strn,
        teams=teams,
        team_conf=team_conf,
        sos=sos_l,
        quality_wins=qw,
        season=season,
        season_progress=progress,
    )
    return [teams[i] for i in field], {teams[i] for i in auto}


def evaluate_final(season: int) -> dict:
    games = _games_for_season(season)
    team_conf = assign_team_conferences(games)
    import pandas as pd

    prior = load_games()
    prior = prior[prior["date"] < f"{season}-08-01"].copy()
    extra = pd.DataFrame(games)
    extra["date"] = pd.to_datetime(extra["date"])
    extra["home_win"] = extra["home_win"].astype(int)
    keep = ["date", "game_id", "home_team", "away_team", "home_win", "neutral_site"]
    hist = pd.concat([prior[keep], extra[keep]], ignore_index=True, sort=False)
    hist["date"] = pd.to_datetime(hist["date"])
    strength = current_elo_ratings(hist)
    rec, _, _, _ = _records_and_champs(games, team_conf)

    sos = defaultdict(list)
    qwins = defaultdict(float)
    for game in games:
        home, away = game["home_team"], game["away_team"]
        hs, aws = strength.get(home, 1500.0), strength.get(away, 1500.0)
        sos[home].append(aws)
        sos[away].append(hs)
        if int(game["home_win"]) == 1 and aws >= 1600:
            qwins[home] += 1
        if int(game["home_win"]) == 0 and hs >= 1600:
            qwins[away] += 1
    sos_avg = {t: (sum(v) / len(v) if v else 1500.0) for t, v in sos.items()}

    teams = sorted(team_conf)
    champs = _detect_champs(rec, team_conf, strength, games)
    pred, auto = _select(season, teams, team_conf, rec, strength, sos_avg, qwins, champs, 1.0)
    actual = list(ACTUAL_FIELDS[season])
    return {
        "season": season,
        "week": "final",
        "hit_rate": field_hit_rate(pred, actual),
        "hits": int(round(field_hit_rate(pred, actual) * 12)),
        "predicted": pred,
        "auto": sorted(auto),
        "champs": champs,
        "misses": misses(pred, actual),
    }


def _preseason_strength(season: int) -> dict[str, float]:
    hist = load_games()
    hist = hist[hist["date"] < f"{season}-08-01"].copy()
    if hist.empty:
        return {}
    hist["date"] = hist["date"]
    return current_elo_ratings(hist)


def evaluate_week(season: int, through_week: int) -> dict:
    """Lock games through *through_week*, project the rest with Elo."""
    games = _games_for_season(season)
    team_conf = assign_team_conferences(games)
    done = [g for g in games if int(g.get("week") or 0) <= through_week]
    left = [g for g in games if int(g.get("week") or 0) > through_week]
    import pandas as pd

    prior = load_games()
    prior = prior[prior["date"] < f"{season}-08-01"].copy()
    pre = regress_offseason(current_elo_ratings(prior)) if not prior.empty else {}
    extra = pd.DataFrame(done)
    if not extra.empty:
        extra["date"] = pd.to_datetime(extra["date"])
        extra["home_win"] = extra["home_win"].astype(int)
        keep = ["date", "game_id", "home_team", "away_team", "home_win", "neutral_site"]
        for col in keep:
            if col not in extra.columns:
                extra[col] = 0 if col != "game_id" else ""
        hist = pd.concat([prior[keep], extra[keep]], ignore_index=True, sort=False)
        hist["date"] = pd.to_datetime(hist["date"])
        current = current_elo_ratings(hist)
    else:
        current = dict(pre)
    progress = min(1.0, through_week / 12.0)
    strength = {}
    for team in set(pre) | set(current) | set(team_conf):
        strength[team] = (1.0 - 0.55 * progress) * float(pre.get(team, 1500.0)) + (
            0.55 * progress
        ) * float(current.get(team, pre.get(team, 1500.0)))
    rec, _, _, _ = _records_and_champs(done, team_conf)
    exp_wins = {t: float(rec.get(t, {}).get("wins", 0)) for t in team_conf}
    exp_conf = {t: float(rec.get(t, {}).get("conf_wins", 0)) for t in team_conf}
    qwins = defaultdict(float)
    sos = defaultdict(list)
    for game in done:
        home, away = game["home_team"], game["away_team"]
        hs, aws = strength.get(home, 1500.0), strength.get(away, 1500.0)
        sos[home].append(aws)
        sos[away].append(hs)
        if int(game["home_win"]) == 1 and aws >= 1600:
            qwins[home] += 1.0
        if int(game["home_win"]) == 0 and hs >= 1600:
            qwins[away] += 1.0
    for game in left:
        home, away = game["home_team"], game["away_team"]
        hs, aws = strength.get(home, 1500.0), strength.get(away, 1500.0)
        p = elo_win_prob(hs, aws, neutral=bool(game.get("neutral_site")))
        exp_wins[home] = exp_wins.get(home, 0.0) + p
        exp_wins[away] = exp_wins.get(away, 0.0) + (1.0 - p)
        sos[home].append(aws)
        sos[away].append(hs)
        if aws >= 1600:
            qwins[home] += p
        if hs >= 1600:
            qwins[away] += 1.0 - p
        if int(game.get("conference_game") or 0) and team_conf.get(home) == team_conf.get(away):
            exp_conf[home] = exp_conf.get(home, 0.0) + p
            exp_conf[away] = exp_conf.get(away, 0.0) + (1.0 - p)
    fake_rec = {
        t: {"wins": exp_wins.get(t, 0.0), "conf_wins": exp_conf.get(t, 0.0), "losses": 0}
        for t in team_conf
    }
    champs = _detect_champs(fake_rec, team_conf, strength, done)
    sos_avg = {t: (sum(v) / len(v) if v else 1500.0) for t, v in sos.items()}
    teams = sorted(team_conf)
    pred, auto = _select(
        season,
        teams,
        team_conf,
        fake_rec,
        strength,
        sos_avg,
        qwins,
        champs,
        min(1.0, through_week / 12.0),
    )
    actual = list(ACTUAL_FIELDS[season])
    return {
        "season": season,
        "week": through_week,
        "hit_rate": field_hit_rate(pred, actual),
        "hits": int(round(field_hit_rate(pred, actual) * 12)),
        "predicted": pred,
        "auto": sorted(auto),
        "misses": misses(pred, actual),
    }


def main() -> None:
    rows = []
    for season in (2024, 2025):
        final = evaluate_final(season)
        rows.append(final)
        print(
            f"{season} FINAL  {final['hits']}/12 ({final['hit_rate']:.0%})  "
            f"in-wrong={final['misses']['false_in']}  out-wrong={final['misses']['false_out']}"
        )
        print(f"  champs: {final['champs']}")
        print(f"  field: {final['predicted']}")
        for week in (0, 3, 6, 9, 12):
            snap = evaluate_week(season, week)
            rows.append(snap)
            print(
                f"{season} W{week:<2}    {snap['hits']}/12 ({snap['hit_rate']:.0%})  "
                f"in-wrong={snap['misses']['false_in']}  out-wrong={snap['misses']['false_out']}"
            )
    out = ROOT / "data" / "processed" / "cfb_playoff_backtest.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    week3 = [r for r in rows if r["week"] == 3]
    avg3 = sum(r["hit_rate"] for r in week3) / len(week3)
    print(f"\nWeek-3 average: {avg3:.0%}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
