"""Walk-forward NFL moneyline backtest (v2 GBR + Elo toss-up override).

    python scripts/backtest_nfl_seasons.py
    python scripts/backtest_nfl_seasons.py --seasons 2023 2024 2025
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.features.nfl_pregame import FEATURE_COLUMNS_V2, build_features_for_history
from app.models.nfl_baseline import (
    ACCURACY_HARD_MIN,
    MODEL_VERSION,
    apply_elo_tossup,
    compute_metrics,
    load_games,
    predict_elo,
    predict_home_rate_constant,
    train_gbr,
    tune_tossup_cut,
)
from app.models.nfl_confidence import (
    apply_categories,
    bin_hit_rates,
    category_proof,
    cumulative_tails,
    fit_category_cuts,
    labeled_proof,
)

REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_season_backtest.json"
GAMES_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_season_backtest_games.json"
CUTS_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_confidence_cuts.json"
DEFAULT_SEASONS = (2024, 2025)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subset_metrics(name: str, y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    if len(y) == 0:
        return {"name": name, "games": 0, "correct": 0, "wrong": 0, "accuracy": None}
    m = compute_metrics(name, y, p)
    picks = (p >= 0.5).astype(int)
    correct = int((picks == y).sum())
    return {
        "name": name,
        "games": int(len(y)),
        "correct": correct,
        "wrong": int(len(y) - correct),
        "accuracy": round(m.accuracy, 4),
        "accuracy_pct": round(m.accuracy * 100, 1),
        "log_loss": round(m.log_loss, 4),
        "brier": round(m.brier, 4),
    }


def _slice_metrics(frame: pd.DataFrame, mask: pd.Series, name: str) -> dict[str, Any]:
    sub = frame.loc[mask]
    return _subset_metrics(
        name,
        sub["home_win"].astype(int).to_numpy(),
        sub["model_prob"].to_numpy(dtype=float),
    )


def _lessons(games: list[dict[str, Any]]) -> list[str]:
    misses = [g for g in games if not g["correct"]]
    if not misses:
        return []
    pre = [g for g in misses if g["game_type"] == "preseason"]
    toss = [g for g in misses if abs(g["home_pct"] - 50) < 5]
    notes = [
        f"{len(misses)} misses in {len(games)} games "
        f"({100 * len(misses) / len(games):.1f}%).",
        f"{len(pre)} misses were preseason "
        f"({100 * len(pre) / max(1, len([g for g in games if g['game_type'] == 'preseason'])):.0f}% of preseason).",
        f"{len(toss)} misses were toss-ups (both sides 45–55%). "
        "Those stay close to a coin flip even after the Elo override.",
    ]
    return notes


def _score_fold(
    feat: pd.DataFrame,
    *,
    test_season: int,
    elo_probs: np.ndarray,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    prior = feat[feat["season"] < test_season]
    test = feat[feat["season"] == test_season].copy()
    if test.empty or prior.empty:
        return None

    prior_seasons = sorted(int(s) for s in prior["season"].unique())
    platt_season = prior_seasons[-1]
    base_seasons = [s for s in prior_seasons if s < platt_season]
    base = prior[prior["season"].isin(base_seasons)] if base_seasons else prior
    platt_df = prior[prior["season"] == platt_season] if base_seasons else prior.iloc[0:0]

    cols = list(FEATURE_COLUMNS_V2)
    model = train_gbr(base, cols)
    raw = model.predict_proba(test[cols].values)[:, 1]
    elo_test = elo_probs[test.index.to_numpy()]
    cut = 0.07
    if len(platt_df) >= 40:
        raw_val = model.predict_proba(platt_df[cols].values)[:, 1]
        elo_val = elo_probs[platt_df.index.to_numpy()]
        cut = tune_tossup_cut(platt_df["home_win"].values, raw_val, elo_val)
    probs = np.clip(apply_elo_tossup(raw, elo_test, cut), 1e-6, 1 - 1e-6)

    test = test.copy()
    test["model_prob"] = probs
    y = test["home_win"].astype(int).to_numpy()
    p = test["model_prob"].to_numpy(dtype=float)

    home_rate_p = predict_home_rate_constant(prior, test)
    pre_mask = test["is_preseason"].fillna(0).astype(int) == 1
    reg_mask = ~pre_mask

    fold = {
        "season": test_season,
        "train_seasons": [int(s) for s in base["season"].unique()],
        "tune_season": platt_season if len(platt_df) >= 40 else None,
        "tossup_cut": cut,
        "train_games": int(len(base) + len(platt_df)),
        "model": _subset_metrics(MODEL_VERSION, y, p),
        "elo": _subset_metrics("elo", y, elo_test),
        "home_rate": _subset_metrics("home_win_rate", y, home_rate_p),
        "regular": _slice_metrics(test, reg_mask, "regular"),
        "preseason": _slice_metrics(test, pre_mask, "preseason"),
    }

    games: list[dict[str, Any]] = []
    for row, prob, actual in zip(test.itertuples(index=False), p, y):
        pick_home = int(prob >= 0.5)
        games.append(
            {
                "season": int(row.season),
                "date": str(pd.Timestamp(row.date).date()),
                "week": int(getattr(row, "week", 0) or 0),
                "game_id": str(row.game_id),
                "away_team": str(row.away_team),
                "home_team": str(row.home_team),
                "game_type": str(getattr(row, "game_type", "regular")),
                "away_pct": round((1.0 - float(prob)) * 100, 1),
                "home_pct": round(float(prob) * 100, 1),
                "pick": str(row.home_team if pick_home else row.away_team),
                "actual": str(row.home_team if actual else row.away_team),
                "winner": str(row.home_team if actual else row.away_team),
                "correct": int(pick_home == actual),
            }
        )
    return fold, games


def run_backtest(seasons: tuple[int, ...] = DEFAULT_SEASONS) -> dict[str, Any]:
    raw = load_games()
    feat = build_features_for_history(raw).reset_index(drop=True)
    elo_probs = predict_elo(feat)

    folds = []
    games: list[dict[str, Any]] = []
    for season in seasons:
        scored = _score_fold(feat, test_season=season, elo_probs=elo_probs)
        if scored is None:
            continue
        fold, fold_games = scored
        folds.append(fold)
        games.extend(fold_games)

    if not folds:
        raise ValueError(f"No scored folds for seasons {seasons}. Check nfl_games.parquet.")

    total_games = sum(f["model"]["games"] for f in folds)
    total_correct = sum(f["model"]["correct"] for f in folds)
    reg_games = sum(f["regular"]["games"] for f in folds)
    reg_correct = sum(f["regular"]["correct"] for f in folds)
    pre_games = sum(f["preseason"]["games"] for f in folds)
    pre_correct = sum(f["preseason"]["correct"] for f in folds)

    regular_games = [g for g in games if g.get("game_type") != "preseason"]
    fit_games = regular_games or games
    cuts = fit_category_cuts(fit_games)
    games = apply_categories(games, cuts)
    regular_labeled = [g for g in games if g.get("game_type") != "preseason"]
    proof_all = category_proof(games, cuts)
    proof_regular = category_proof(regular_labeled, cuts)
    proof_labeled = labeled_proof(games)
    proof_labeled_regular = labeled_proof(regular_labeled)
    bins5_all = bin_hit_rates(games, width=5.0)
    bins5_regular = bin_hit_rates(regular_labeled, width=5.0)
    bins5_by_season = {
        str(season): bin_hit_rates([g for g in regular_labeled if g["season"] == season], width=5.0)
        for season in seasons
    }

    by_season_cat: dict[int, dict[str, dict[str, int]]] = {}
    for g in games:
        season_row = by_season_cat.setdefault(g["season"], {})
        cat = season_row.setdefault(g["category"], {"games": 0, "correct": 0})
        cat["games"] += 1
        cat["correct"] += int(g["correct"])

    report = {
        "generated_at": _iso_now(),
        "method": (
            "Walk-forward v2: GBR on form/rest/travel/pythagorean features, "
            "Elo override when the model is inside a toss-up band tuned on the prior season. "
            "Categories fit on regular-season favorite-% hit rates for the scored years. "
            "Lock may be an interior band when a higher-% upset breaks the tail. "
            "Preseason is capped at soft. No future games in train. No API calls."
        ),
        "feature_set": "nfl_v2",
        "model": MODEL_VERSION,
        "goals": {
            "hard_min_pct": ACCURACY_HARD_MIN * 100,
            "soft_floor_pct": 60,
            "hard_floor_pct": 75,
            "lock_floor_pct": 95,
        },
        "seasons": [f["season"] for f in folds],
        "folds": folds,
        "categories": cuts,
        "category_proof": {
            "all_games": proof_all,
            "regular": proof_regular,
            "labeled_all": proof_labeled,
            "labeled_regular": proof_labeled_regular,
        },
        "favorite_bins_5pct": {
            "all_games": bins5_all,
            "regular": bins5_regular,
            "by_season_regular": bins5_by_season,
        },
        "favorite_bins": bins5_regular,
        "tail_proof": {
            "note": (
                "When we post a favorite at X%, this is how often that pick hits. "
                "Hard needs a 75% band with 15+ regular-season games. "
                "Lock needs 95% with 8+ games and may stop before 100% if a "
                "higher favorite-% upset would break the tail."
            ),
            "all_years": cumulative_tails(games),
            "regular": cumulative_tails(regular_labeled),
        },
        "category_by_season": {
            str(season): {
                name: {
                    "games": vals["games"],
                    "correct": vals["correct"],
                    "hit_pct": round(100 * vals["correct"] / vals["games"], 1),
                }
                for name, vals in cats.items()
            }
            for season, cats in sorted(by_season_cat.items())
        },
        "aggregate": {
            "games": total_games,
            "correct": total_correct,
            "wrong": total_games - total_correct,
            "accuracy": round(total_correct / total_games, 4) if total_games else None,
            "accuracy_pct": round(100 * total_correct / total_games, 1) if total_games else None,
            "hard_min_met": all(f["model"]["accuracy"] >= ACCURACY_HARD_MIN for f in folds),
            "regular": {
                "games": reg_games,
                "correct": reg_correct,
                "accuracy_pct": round(100 * reg_correct / reg_games, 1) if reg_games else None,
            },
            "preseason": {
                "games": pre_games,
                "correct": pre_correct,
                "accuracy_pct": round(100 * pre_correct / pre_games, 1) if pre_games else None,
            },
        },
        "lessons": _lessons(games),
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    GAMES_JSON.write_text(json.dumps(games, indent=2), encoding="utf-8")
    CUTS_JSON.write_text(json.dumps(cuts, indent=2), encoding="utf-8")
    report["games"] = games
    return report


def _print_report(report: dict[str, Any]) -> None:
    print(report["method"])
    print()
    print(f"{'Season':<8} {'Games':>6} {'Correct':>8} {'Wrong':>6} {'Hit %':>7} {'Elo %':>7} {'Reg %':>7} {'Pre %':>7}")
    for fold in report["folds"]:
        m = fold["model"]
        print(
            f"{fold['season']:<8} {m['games']:>6} {m['correct']:>8} {m['wrong']:>6} "
            f"{m['accuracy_pct']:>6.1f}% "
            f"{fold['elo']['accuracy_pct']:>6.1f}% "
            f"{(fold['regular']['accuracy_pct'] or 0):>6.1f}% "
            f"{(fold['preseason']['accuracy_pct'] or 0):>6.1f}%"
        )
    agg = report["aggregate"]
    print("-" * 72)
    years = "-".join(str(s) for s in report.get("seasons") or [])
    print(
        f"{years:<8} {agg['games']:>6} {agg['correct']:>8} {agg['wrong']:>6} "
        f"{agg['accuracy_pct']:>6.1f}%"
    )
    print(
        f"Hard min 60% every season: {agg['hard_min_met']} | "
        f"Regular {agg['regular']['accuracy_pct']}% | "
        f"Preseason {agg['preseason']['accuracy_pct']}%"
    )
    print("\nWhen we post this favorite %, how often the pick hits (regular season):")
    for row in report.get("favorite_bins_5pct", {}).get("regular") or []:
        print(
            f"  {row['favorite_range']:<10} {row['games']:>4} games  "
            f"{row['correct']:>3} correct  {row['hit_pct']}%"
        )
    print("\nCategories (regular-season labels):")
    for row in report.get("category_proof", {}).get("labeled_regular", []):
        hit = f"{row['hit_pct']}%" if row.get("hit_pct") is not None else "n/a"
        floor = f"floor {row['floor_pct']}%" if row.get("floor_pct") else "no floor"
        flag = "OK" if row.get("meets_floor") else "NO"
        print(
            f"  {row['category']:<8} {row['games']:>5} games  "
            f"{row['correct']:>4} correct  {hit:>6}  {floor}  {flag}"
        )
    print(f"\nWrote {REPORT_JSON}")
    print(f"Wrote {GAMES_JSON} ({len(report.get('games') or [])} games)")
    print(f"Wrote {CUTS_JSON}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward NFL season backtest")
    parser.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=list(DEFAULT_SEASONS),
        help="Test seasons (default: 2024 2025)",
    )
    args = parser.parse_args()
    report = run_backtest(tuple(args.seasons))
    _print_report(report)


if __name__ == "__main__":
    main()
