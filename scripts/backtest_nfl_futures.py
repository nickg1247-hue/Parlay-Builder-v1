"""Walk-forward preseason division-place backtest (2023–2025).

Uses only regular-season results from prior years, then the upcoming
schedule, to rank each division 1–4 before Week 1.

    python scripts/backtest_nfl_futures.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.ingest.nfl import NFL_DIVISIONS, normalize_abbr
from app.models.nfl_baseline import load_games
from app.services.nfl_division_priors import (
    PLACE_BONUS,
    WINS_BLEND,
    YEAR_WEIGHTS,
    actual_division_places,
    prior_game_probs,
    projected_wins_from_history,
    season_team_stats,
    wins_to_elo,
)
from app.services.nfl_futures import DIVISION_SPECS, project_from_probs

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_futures_backtest.json"
DEFAULT_SEASONS = (2023, 2024, 2025)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schedule_rows(df, season: int) -> list[dict[str, Any]]:
    games = df[(df["season"] == int(season)) & (df["game_type"] == "regular")]
    rows = []
    for row in games.itertuples(index=False):
        home = normalize_abbr(row.home_team_abbr)
        away = normalize_abbr(row.away_team_abbr)
        rows.append(
            {
                "game_id": str(row.game_id),
                "date": str(row.date)[:10],
                "season": int(season),
                "week": int(getattr(row, "week", 0) or 0),
                "home_team": home,
                "away_team": away,
                "home_team_abbr": home,
                "away_team_abbr": away,
                "divisional": int(getattr(row, "divisional", 0) or 0),
                "neutral_site": int(getattr(row, "neutral_site", 0) or 0),
                "completed": False,
                "home_win": None,
                "tie": False,
            }
        )
    return rows


def _score(pred: dict[str, int], actual: dict[str, int]) -> dict[str, Any]:
    teams = [t for t in actual if t in NFL_DIVISIONS]
    exact = sum(1 for t in teams if pred.get(t) == actual[t])
    within1 = sum(1 for t in teams if abs(pred.get(t, 99) - actual[t]) <= 1)
    winners = 0
    for div in set(NFL_DIVISIONS.values()):
        act = next(t for t, p in actual.items() if NFL_DIVISIONS[t] == div and p == 1)
        pr = next(t for t, p in pred.items() if NFL_DIVISIONS[t] == div and p == 1)
        winners += int(act == pr)
    n = len(teams)
    return {
        "teams": n,
        "exact": exact,
        "exact_pct": round(100 * exact / n, 1),
        "within1": within1,
        "within1_pct": round(100 * within1 / n, 1),
        "winners": winners,
        "winner_pct": round(100 * winners / 8, 1),
    }


def _places_from_standings(standings: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    out = {}
    for rows in standings.values():
        for row in rows:
            out[row["abbr"]] = int(row["place"])
    return out


def project_season(df, season: int, *, n_sims: int = 400) -> dict[str, Any]:
    remaining = _schedule_rows(df, season)
    prior_wins = projected_wins_from_history(df, season)
    elo = wins_to_elo(prior_wins)
    probs = prior_game_probs(remaining, elo)
    standings = project_from_probs(
        records={},
        remaining=remaining,
        probs=probs,
        strength=elo,
        team_meta={t: {"team": t, "abbr": t, "logo_url": ""} for t in NFL_DIVISIONS},
        n_sims=n_sims,
        seed=int(season),
    )
    pred = _places_from_standings(standings)
    actual_stats = season_team_stats(df, season)
    actual = actual_division_places(actual_stats)
    last = actual_division_places(season_team_stats(df, season - 1))
    score = _score(pred, actual)
    divisions = []
    for spec in DIVISION_SPECS:
        teams = [t for t, d in NFL_DIVISIONS.items() if d == spec["key"]]
        rows = []
        for team in sorted(teams, key=lambda t: actual[t]):
            proj = next(r for r in standings[spec["key"]] if r["abbr"] == team)
            rows.append(
                {
                    "team": team,
                    "actual_place": actual[team],
                    "pred_place": pred[team],
                    "last_place": last.get(team),
                    "actual_wins": int(actual_stats.get(team, {}).get("wins", 0)),
                    "prior_wins": round(prior_wins.get(team, 8.5), 2),
                    "expected_wins": proj["expected_wins"],
                    "division_win_pct": proj["division_win_pct"],
                    "hit": pred[team] == actual[team],
                    "within1": abs(pred[team] - actual[team]) <= 1,
                }
            )
        divisions.append({"key": spec["key"], "name": spec["name"], "teams": rows})
    return {"season": season, "score": score, "divisions": divisions}


def run_backtest(seasons: tuple[int, ...] = DEFAULT_SEASONS, *, n_sims: int = 400) -> dict[str, Any]:
    df = load_games()
    folds = [project_season(df, season, n_sims=n_sims) for season in seasons]
    tot = {"teams": 0, "exact": 0, "within1": 0, "winners": 0, "divs": 0}
    for fold in folds:
        s = fold["score"]
        tot["teams"] += s["teams"]
        tot["exact"] += s["exact"]
        tot["within1"] += s["within1"]
        tot["winners"] += s["winners"]
        tot["divs"] += 8
    summary = {
        "teams": tot["teams"],
        "exact": tot["exact"],
        "exact_pct": round(100 * tot["exact"] / tot["teams"], 1),
        "within1": tot["within1"],
        "within1_pct": round(100 * tot["within1"] / tot["teams"], 1),
        "winners": tot["winners"],
        "winner_pct": round(100 * tot["winners"] / tot["divs"], 1),
    }
    return {
        "generated_at": _iso_now(),
        "seasons": list(seasons),
        "method": {
            "year_weights": list(YEAR_WEIGHTS),
            "wins_blend": WINS_BLEND,
            "place_bonus": list(PLACE_BONUS),
        },
        "summary": summary,
        "folds": folds,
    }


def _print_report(report: dict[str, Any]) -> None:
    print("NFL preseason division-place backtest")
    print(
        f"method year_weights={report['method']['year_weights']} "
        f"wins_blend={report['method']['wins_blend']}"
    )
    for fold in report["folds"]:
        s = fold["score"]
        print(
            f"\n{fold['season']}: exact {s['exact']}/{s['teams']} ({s['exact_pct']}%)  "
            f"within1 {s['within1']}/{s['teams']} ({s['within1_pct']}%)  "
            f"winners {s['winners']}/8 ({s['winner_pct']}%)"
        )
        for div in fold["divisions"]:
            bits = []
            for row in sorted(div["teams"], key=lambda r: r["pred_place"]):
                mark = "OK" if row["hit"] else ("~" if row["within1"] else "X")
                bits.append(
                    f"{row['pred_place']} {row['team']} "
                    f"(act {row['actual_place']} {row['actual_wins']}-W, last {row['last_place']}) {mark}"
                )
            print(f"  {div['name']}: " + " | ".join(bits))
    s = report["summary"]
    print(
        f"\n3-year: exact {s['exact']}/{s['teams']} ({s['exact_pct']}%)  "
        f"within1 {s['within1']}/{s['teams']} ({s['within1_pct']}%)  "
        f"winners {s['winners']}/24 ({s['winner_pct']}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest NFL preseason division ranks")
    parser.add_argument("--seasons", nargs="+", type=int, default=list(DEFAULT_SEASONS))
    parser.add_argument("--sims", type=int, default=400)
    args = parser.parse_args()
    report = run_backtest(tuple(args.seasons), n_sims=args.sims)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _print_report(report)
    print(f"\nWrote {REPORT_JSON}")


if __name__ == "__main__":
    main()
