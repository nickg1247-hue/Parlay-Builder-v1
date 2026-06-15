"""Evaluate CFB model vs market on holdout season. Run from project root."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.cfb_market_eval import format_summary_table, run_market_evaluation

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate production CFB moneyline model vs vig-free market odds "
            "on 2025 holdout. Uses CFBD lines cache + live-captured "
            "cfb_odds_repository (no bulk historical Odds API)."
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
    if results["matched_games"] == 0:
        print(
            "\nNo odds matched. See MARKET_CFB.md — CFBD lines cache or capture live "
            "lines to data/processed/cfb_odds_repository/."
        )
