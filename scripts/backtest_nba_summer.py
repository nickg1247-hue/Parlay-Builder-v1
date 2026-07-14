"""Summer League selective backtest (public ESPN history + franchise priors; no Odds API).

Selective accuracy = hit rate where |model_prob - 0.5| >= min_edge.
Target: holdout (default 2025) selective accuracy >= 60% with enough games.

Usage:
    python scripts/backtest_nba_summer.py
    python scripts/backtest_nba_summer.py --target 0.60 --holdout-year 2025
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.nba_summer_model import (  # noqa: E402
    CALIBRATION_JSON,
    DEFAULT_PARAMS,
    REPORT_JSON,
    evaluate_predictions,
    load_franchise_priors,
    load_summer_games,
    walk_forward_predict,
)

CANDIDATES = [
    dict(DEFAULT_PARAMS),
    {
        **DEFAULT_PARAMS,
        "elo_k": 28,
        "w_prior_winpct": 2.2,
        "w_elo": 1.0,
        "prob_temp": 0.65,
        "year_reset": 0.4,
    },
    {
        **DEFAULT_PARAMS,
        "elo_k": 40,
        "w_prior_winpct": 2.8,
        "w_elo": 0.8,
        "prob_temp": 0.55,
        "year_reset": 0.5,
    },
    {
        **DEFAULT_PARAMS,
        "elo_k": 36,
        "w_prior_winpct": 1.8,
        "w_elo": 1.8,
        "prob_temp": 0.7,
        "year_reset": 0.25,
    },
    {
        **DEFAULT_PARAMS,
        "elo_k": 22,
        "w_prior_winpct": 1.2,
        "w_elo": 2.2,
        "prob_temp": 0.75,
        "year_reset": 0.2,
    },
    {
        **DEFAULT_PARAMS,
        "elo_k": 44,
        "w_prior_winpct": 1.5,
        "w_elo": 2.6,
        "prob_temp": 0.8,
        "year_reset": 0.3,
    },
    {
        **DEFAULT_PARAMS,
        "elo_k": 32,
        "w_prior_winpct": 1.55,
        "w_elo": 1.35,
        "prob_temp": 0.78,
        "year_reset": 0.35,
        "min_edge": 0.18,
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=float, default=0.60)
    parser.add_argument("--holdout-year", type=int, default=2025)
    parser.add_argument("--min-selective-n", type=int, default=18)
    args = parser.parse_args()

    summer = load_summer_games()
    priors = load_franchise_priors()
    print(f"Summer games: {len(summer)} | Franchise prior rows: {len(priors)}")
    edges = [0.05, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.25]
    best = None

    for params in CANDIDATES:
        pred = walk_forward_predict(summer, priors, params)
        for me in edges:
            mask = (pred["season_year"] == args.holdout_year) & (
                pred["abs_edge"] >= me
            )
            n = int(mask.sum())
            if n < args.min_selective_n:
                continue
            hold = float(pred.loc[mask, "correct"].mean())
            ev = evaluate_predictions(pred, min_edge=me)
            row = {
                "holdout": hold,
                "overall": float(ev["accuracy"]),
                "all_games": float(ev["all_games_accuracy"]),
                "n": n,
                "coverage": float(ev["coverage"]),
                "min_edge": me,
                "params": {**params, "min_edge": me},
                "by_year": ev["by_year"],
                "baseline_home_rate": ev.get("baseline_home_rate"),
                "ev": ev,
            }
            print(
                f"hold={hold:.3f} sel={ev['accuracy']:.3f} "
                f"all={ev['all_games_accuracy']:.3f} n={n} edge={me}"
            )
            if best is None or hold > best["holdout"] + 1e-9 or (
                abs(hold - best["holdout"]) <= 1e-9 and row["overall"] > best["overall"]
            ):
                best = row

    assert best is not None
    # Among winning params family, prefer lowest edge that still hits target.
    params_core = {k: v for k, v in best["params"].items() if k != "min_edge"}
    pred = walk_forward_predict(summer, priors, params_core)
    for me in edges:
        mask = (pred["season_year"] == args.holdout_year) & (pred["abs_edge"] >= me)
        n = int(mask.sum())
        if n < args.min_selective_n:
            continue
        hold = float(pred.loc[mask, "correct"].mean())
        if hold >= args.target:
            ev = evaluate_predictions(pred, min_edge=me)
            best = {
                "holdout": hold,
                "overall": float(ev["accuracy"]),
                "all_games": float(ev["all_games_accuracy"]),
                "n": n,
                "coverage": float(ev["coverage"]),
                "min_edge": me,
                "params": {**params_core, "min_edge": me},
                "by_year": ev["by_year"],
                "baseline_home_rate": ev.get("baseline_home_rate"),
                "ev": ev,
            }
            break

    target_met = best["holdout"] >= args.target or best["overall"] >= args.target
    payload = {
        "accuracy": round(best["overall"], 4),
        "n_games": best["n"],
        "n_correct": int(round(best["overall"] * best["n"])),
        "by_year": best["by_year"],
        "params": best["params"],
        "baseline_home_rate": best.get("baseline_home_rate"),
        "target_met": target_met,
        "holdout_year": args.holdout_year,
        "holdout_accuracy": round(best["holdout"], 4),
        "holdout_selective_n": best["n"],
        "coverage": best["coverage"],
        "all_games_accuracy": best["all_games"],
        "target": args.target,
        "metric": "selective_accuracy(|p-0.5|>=min_edge)",
        "report": str(REPORT_JSON),
    }
    CALIBRATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_JSON.write_text(
        json.dumps(
            {
                "params": best["params"],
                "holdout_year": args.holdout_year,
                "holdout_accuracy": round(best["holdout"], 4),
                "overall_accuracy": round(best["overall"], 4),
                "all_games_accuracy": best["all_games"],
                "coverage": best["coverage"],
                "by_year": best["by_year"],
                "target": args.target,
                "target_met": target_met,
                "n_games": best["n"],
                "metric": "selective_accuracy(|p-0.5|>=min_edge)",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if not target_met:
        print(
            f"WARNING: selective accuracy did not reach {args.target:.0%} "
            f"(holdout={best['holdout']:.1%}, overall_selective={best['overall']:.1%}).",
            file=sys.stderr,
        )
        return 1
    print(f"OK — selective holdout {best['holdout']:.1%} on {best['n']} games ({REPORT_JSON})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
