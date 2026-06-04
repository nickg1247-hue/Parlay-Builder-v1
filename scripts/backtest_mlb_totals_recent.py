"""Recent-window totals backtest (edge-flagged hit rate)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.data.mlb_games import load_games_with_totals
from app.features.mlb_totals_pregame import TOTALS_FEATURE_COLUMNS, build_totals_features
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.mlb_totals import (
    METRICS_JSON,
    actual_went_over,
    edge_flagged_hit_rate,
    load_totals_artifact,
    prob_over_poisson,
)
from app.odds.mlb_odds_free import TOTALS_2025_CSV
from app.odds.odds_math import market_probs_from_american_totals

OUTPUT = PROJECT_ROOT / "data" / "processed" / "mlb_totals_backtest_recent.json"


def run_backtest(days: int = 7) -> dict:
    games = load_games_with_totals()
    games = games[games["season"] == 2025].copy()
    if games.empty or not TOTALS_2025_CSV.exists():
        return {"days": days, "error": "Need 2025 games and totals odds CSV", "games": 0}

    max_date = games["date"].max()
    start = max_date - timedelta(days=days)
    window = games[games["date"] >= start].copy()

    artifact = load_totals_artifact()
    reg = artifact["model"]
    cols = artifact["feature_columns"]

    hist = games[games["date"] < start].copy()
    combined = pd.concat([hist, window], ignore_index=True)
    feat = build_totals_features(combined, update_state=True)
    eval_df = feat[feat["game_id"].isin(window["game_id"].astype(str))].copy()

    odds = pd.read_csv(TOTALS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    eval_df["date"] = pd.to_datetime(eval_df["date"]).dt.strftime("%Y-%m-%d")
    merged = eval_df.merge(
        odds, on=["date", "home_team", "away_team"], how="inner"
    )
    if merged.empty:
        return {"days": days, "error": "No matched O/U lines in window", "games": 0}

    merged["expected_total_runs"] = reg.predict(merged[cols].values)
    merged["model_prob_over"] = [
        prob_over_poisson(float(mu), float(line))
        for mu, line in zip(merged["expected_total_runs"], merged["ou_line"])
    ]
    market_o = []
    for row in merged.itertuples(index=False):
        mo, _ = market_probs_from_american_totals(int(row.over_odds), int(row.under_odds))
        market_o.append(mo)
    merged["market_prob_over"] = market_o
    merged["went_over"] = merged.apply(
        lambda r: actual_went_over(r.total_runs, float(r.ou_line)), axis=1
    )
    hit = edge_flagged_hit_rate(merged, "model_prob_over", "market_prob_over")

    payload = {
        "days": days,
        "start_date": str(start.date()),
        "end_date": str(max_date.date()),
        "games_evaluated": len(merged),
        "hit_rate_edge_flagged": hit,
        "min_edge": DEFAULT_MIN_EDGE,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    result = run_backtest(args.days)
    print(json.dumps(result, indent=2))
