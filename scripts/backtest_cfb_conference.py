"""Conference finish accuracy vs 2024–2025 actual regular-season order."""

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
from app.services.cfb_futures import (
    CONFERENCES,
    INDEPENDENT_KEY,
    assign_team_conferences,
    elo_win_prob,
    mark_title_games,
    match_conference,
)
from app.services.cfb_playoff import regress_offseason
from app.services.cfb_preseason import (
    blend_scores,
    leftover_elo,
    load_preseason_components,
    mix_preseason_strength,
    pick_conference_favorite,
)
from scripts.backtest_cfb_playoff import _games_for_season, _records_and_champs


def _actual_order(games, team_conf, strength):
    rec, _, _, _ = _records_and_champs(
        [g for g in games if not g.get("title_game")], team_conf
    )
    by_conf = defaultdict(list)
    for team, conf in team_conf.items():
        if conf == INDEPENDENT_KEY:
            continue
        by_conf[conf].append(team)
    order = {}
    for conf, teams in by_conf.items():
        teams.sort(
            key=lambda t: (
                -rec.get(t, {}).get("conf_wins", 0),
                rec.get(t, {}).get("conf_losses", 0),
                -rec.get(t, {}).get("wins", 0),
                -float(strength.get(t, 1500.0)),
                t,
            )
        )
        order[conf] = teams
    champs = {}
    for game in games:
        if not game.get("title_game") or game.get("home_win") is None:
            continue
        spec = match_conference(str(game.get("home_conference") or ""))
        if spec is None:
            continue
        winner = game["home_team"] if int(game["home_win"]) else game["away_team"]
        champs[spec["key"]] = winner
    for conf, teams in order.items():
        champs.setdefault(conf, teams[0] if teams else "")
    return order, champs, rec


def _projected_order(done, left, team_conf, strength):
    rec, _, _, _ = _records_and_champs(
        [g for g in done if not g.get("title_game")], team_conf
    )
    exp_conf = {t: float(rec.get(t, {}).get("conf_wins", 0)) for t in team_conf}
    exp_win = {t: float(rec.get(t, {}).get("wins", 0)) for t in team_conf}
    for game in left:
        if game.get("title_game"):
            continue
        home, away = game["home_team"], game["away_team"]
        p = elo_win_prob(
            strength.get(home, 1500.0),
            strength.get(away, 1500.0),
            neutral=bool(game.get("neutral_site")),
        )
        exp_win[home] = exp_win.get(home, 0.0) + p
        exp_win[away] = exp_win.get(away, 0.0) + (1.0 - p)
        same = (
            team_conf.get(home)
            and team_conf.get(home) == team_conf.get(away)
            and team_conf.get(home) != INDEPENDENT_KEY
        )
        conf_flag = int(game.get("conference_game") or 0) or same
        if same and conf_flag:
            exp_conf[home] = exp_conf.get(home, 0.0) + p
            exp_conf[away] = exp_conf.get(away, 0.0) + (1.0 - p)
    by_conf = defaultdict(list)
    for team, conf in team_conf.items():
        if conf == INDEPENDENT_KEY:
            continue
        by_conf[conf].append(team)
    order = {}
    for conf, teams in by_conf.items():
        teams.sort(
            key=lambda t: (
                -exp_conf.get(t, 0.0),
                -exp_win.get(t, 0.0),
                -float(strength.get(t, 1500.0)),
                t,
            )
        )
        order[conf] = teams
    return order


def _preseason_title_picks(season, team_conf) -> dict[str, str]:
    by_conf = defaultdict(list)
    for team, conf in team_conf.items():
        if conf != INDEPENDENT_KEY:
            by_conf[conf].append(team)
    elo = leftover_elo(season)
    all_teams = [team for teams in by_conf.values() for team in teams]
    components = load_preseason_components(season, all_teams, elo=elo)
    picks = {}
    for conf, teams in by_conf.items():
        if len(teams) < 4:
            continue
        conf_comp = {team: components[team] for team in teams if team in components}
        scores = blend_scores(conf_comp)
        picks[conf] = pick_conference_favorite(teams, scores, conf_comp)
    return picks


def _strength_at(season, done, team_conf, through_week):
    import pandas as pd

    prior = load_games()
    prior = prior[prior["date"] < f"{season}-08-01"].copy()
    pre = regress_offseason(current_elo_ratings(prior)) if not prior.empty else {}
    extra = pd.DataFrame(done)
    keep = ["date", "game_id", "home_team", "away_team", "home_win", "neutral_site"]
    if not extra.empty:
        extra["date"] = pd.to_datetime(extra["date"])
        extra["home_win"] = extra["home_win"].astype(int)
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
    return mix_preseason_strength(
        season=season,
        team_conf=team_conf,
        live=strength,
        through_week=through_week,
    )


def _score(pred, actual, champs_pred, champs_act):
    place_err = []
    exact = within1 = within2 = champ = last = top2 = 0
    n_teams = 0
    n_conf = 0
    per = []
    names = {c["key"]: c["name"] for c in CONFERENCES}
    for conf, act_teams in actual.items():
        pred_teams = pred.get(conf) or []
        if len(act_teams) < 4:
            continue
        n_conf += 1
        pred_place = {t: i + 1 for i, t in enumerate(pred_teams)}
        act_place = {t: i + 1 for i, t in enumerate(act_teams)}
        errs = []
        for team, ap in act_place.items():
            pp = pred_place.get(team)
            if pp is None:
                continue
            n_teams += 1
            d = abs(pp - ap)
            errs.append(d)
            place_err.append(d)
            if d == 0:
                exact += 1
            if d <= 1:
                within1 += 1
            if d <= 2:
                within2 += 1
        if pred_teams and act_teams and pred_teams[0] == act_teams[0]:
            champ += 0  # regular-season #1 tracked separately
        rs_champ_hit = bool(pred_teams and act_teams and pred_teams[0] == act_teams[0])
        title_hit = champs_pred.get(conf) == champs_act.get(conf)
        last_hit = bool(pred_teams and act_teams and pred_teams[-1] == act_teams[-1])
        top2_hit = set(pred_teams[:2]) == set(act_teams[:2])
        if rs_champ_hit:
            champ += 1
        if last_hit:
            last += 1
        if top2_hit:
            top2 += 1
        per.append(
            {
                "conf": names.get(conf, conf),
                "key": conf,
                "pred_1": pred_teams[0] if pred_teams else "",
                "act_1": act_teams[0] if act_teams else "",
                "title_pred": champs_pred.get(conf, ""),
                "title_act": champs_act.get(conf, ""),
                "rs_champ": rs_champ_hit,
                "title_champ": title_hit,
                "last": last_hit,
                "mae": round(sum(errs) / len(errs), 2) if errs else None,
                "exact": sum(1 for d in errs if d == 0),
                "teams": len(errs),
            }
        )
    return {
        "conferences": n_conf,
        "teams": n_teams,
        "champ_pct": champ / n_conf if n_conf else 0,
        "title_pct": sum(1 for r in per if r["title_champ"]) / n_conf if n_conf else 0,
        "top2_pct": top2 / n_conf if n_conf else 0,
        "last_pct": last / n_conf if n_conf else 0,
        "exact_pct": exact / n_teams if n_teams else 0,
        "within1_pct": within1 / n_teams if n_teams else 0,
        "within2_pct": within2 / n_teams if n_teams else 0,
        "mae": round(sum(place_err) / len(place_err), 2) if place_err else None,
        "per": per,
    }


def evaluate(season: int, through_week: int) -> dict:
    games = _games_for_season(season)
    team_conf = assign_team_conferences(games)
    end_strength = _strength_at(season, games, team_conf, 15)
    actual, champs_act, _ = _actual_order(games, team_conf, end_strength)
    done = [g for g in games if int(g.get("week") or 0) <= through_week]
    left = [g for g in games if int(g.get("week") or 0) > through_week]
    strength = _strength_at(season, done, team_conf, through_week)
    pred = _projected_order(done, left, team_conf, strength)
    if through_week < 3:
        champs_pred = _preseason_title_picks(season, team_conf)
    else:
        champs_pred = {conf: teams[0] for conf, teams in pred.items() if teams}
    scored = _score(pred, actual, champs_pred, champs_act)
    scored.update({"season": season, "week": through_week})
    return scored


def main() -> None:
    rows = []
    for season in (2024, 2025):
        ensure_season_schedule(season, force=False)
        for week in (0, 3, 6, 9, 12):
            row = evaluate(season, week)
            rows.append(row)
            print(
                f"{season} W{week:<2}  champ {row['champ_pct']:.0%}  "
                f"title {row['title_pct']:.0%}  top2 {row['top2_pct']:.0%}  "
                f"last {row['last_pct']:.0%}  exact {row['exact_pct']:.0%}  "
                f"±1 {row['within1_pct']:.0%}  ±2 {row['within2_pct']:.0%}  "
                f"MAE {row['mae']}"
            )
            if week in (0, 3, 12):
                for c in row["per"]:
                    if week == 0:
                        mark = "Y" if c["title_champ"] else "n"
                        print(
                            f"    {c['conf']:<22} pred {c['title_pred']:<18} "
                            f"act {c['title_act']:<18} {mark}  MAE {c['mae']}"
                        )
                    else:
                        mark = "Y" if c["rs_champ"] else "n"
                        print(
                            f"    {c['conf']:<22} pred {c['pred_1']:<18} "
                            f"act {c['act_1']:<18} {mark}  MAE {c['mae']}"
                        )
    out = ROOT / "data" / "processed" / "cfb_conference_backtest.json"
    slim = [{k: v for k, v in r.items() if k != "per"} | {"per": r["per"]} for r in rows]
    out.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
