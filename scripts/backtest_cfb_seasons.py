"""Walk-forward CFB backtest across saved seasons — writes cfb_backtest_report.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cfb_backtest_report import REPORT_JSON, run_cfb_walk_forward_backtest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CFB walk-forward backtest on saved cfb_games.parquet"
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print JSON only; do not write cache file",
    )
    args = parser.parse_args()

    report = run_cfb_walk_forward_backtest(write_cache=not args.no_write)

    print(json.dumps(report, indent=2))

    agg = report.get("aggregate", {})
    proof = report.get("proof_summary", {})
    feats = report.get("feature_effects", {}).get("logistic_importance_avg", [])[:5]

    print("\n--- Summary ---")
    print(f"Seasons: {report.get('seasons_available')}")
    print(f"Holdout games scored: {agg.get('holdout_games_scored')}")
    print(f"ML accuracy (weighted): {agg.get('moneyline_accuracy_pct')}%")
    print(f"ML log loss (weighted): {agg.get('moneyline_log_loss')}")
    print(f"Beats naive every fold: {agg.get('beats_naive_every_fold')}")
    print(f"Verdict: {proof.get('verdict')}")
    if feats:
        print("Top features (avg |logistic coef|):")
        for row in feats:
            print(f"  {row['feature']}: {row['avg_abs_logistic_coef']}")
    if not args.no_write:
        print(f"\nWrote {REPORT_JSON}")


if __name__ == "__main__":
    main()
