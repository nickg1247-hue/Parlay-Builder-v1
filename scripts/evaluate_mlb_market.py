"""Evaluate model vs market on 2025 holdout. Run from project root."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.market_eval import format_summary_table, run_market_evaluation

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=0.02,
        help="Minimum edge to flag +EV (default 0.02)",
    )
    args = parser.parse_args()
    results = run_market_evaluation(edge_threshold=args.edge_threshold)
    print(format_summary_table(results))
