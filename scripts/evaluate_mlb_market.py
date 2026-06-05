"""Evaluate model vs market on 2025 holdout. Run from project root."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.market_eval import format_summary_table, run_market_evaluation

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate production moneyline model vs free-market odds on 2025 holdout. "
            "Uses predict_home_win_proba() → v3_logistic_pruned_platt when that artifact is installed."
        )
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help=f"Minimum edge to flag +EV (default {DEFAULT_MIN_EDGE})",
    )
    args = parser.parse_args()
    results = run_market_evaluation(edge_threshold=args.edge_threshold)
    print(format_summary_table(results))
