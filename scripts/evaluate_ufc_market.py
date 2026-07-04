"""Import free UFC holdout moneylines CSV. Run from project root."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.ufc_market_eval import format_summary_table, run_market_evaluation
from app.odds.ufc_odds_free import ODDS_2024_CSV, import_csv, load_csv_odds


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "UFC market eval or import free moneyline CSV for 2024 holdout. "
            "Expected columns: date, home_team, away_team, home_ml, away_ml."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="Path to source CSV to import",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=ODDS_2024_CSV,
        help=f"Output path (default {ODDS_2024_CSV})",
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=0.08,
        help="Minimum model edge for +EV flag (default 0.08)",
    )
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Run market evaluation without importing",
    )
    args = parser.parse_args()

    if args.source is not None:
        dest = import_csv(args.source, args.dest)
        df = load_csv_odds(dest)
        print(f"Imported {len(df)} rows → {dest}")

    if args.eval_only or args.source is None:
        results = run_market_evaluation(edge_threshold=args.edge_threshold)
        print(format_summary_table(results))
        if results.get("status") == "no_odds":
            print("\nNo odds matched. See MARKET_UFC.md for import instructions.")
            sys.exit(1)


if __name__ == "__main__":
    main()
