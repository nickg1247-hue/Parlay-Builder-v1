"""Backfill closing odds on forward CLV log rows (run afternoon/evening)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.forward_clv import backfill_closing_odds as backfill_mlb_clv
from app.services.forward_clv import summarize_clv as summarize_mlb_clv
from app.services.nba_forward_clv import backfill_closing_odds as backfill_nba_clv
from app.services.nba_forward_clv import summarize_clv as summarize_nba_clv
from app.services.ufc_forward_clv import backfill_closing_odds as backfill_ufc_clv
from app.services.ufc_forward_clv import summarize_clv as summarize_ufc_clv
from app.services.prop_pick_tracker import (
    backfill_prop_results,
    summarize_prop_tracker,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill closing lines for forward CLV log")
    parser.add_argument(
        "--sport",
        choices=("mlb", "nba", "ufc"),
        default="mlb",
        help="Sport log to backfill (default: mlb)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Limit to board_date YYYY-MM-DD (default: all open rows)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute updates without writing new log rows (NBA/UFC only)",
    )
    parser.add_argument(
        "--props",
        action="store_true",
        help="Also backfill MLB player prop results (hit/miss grading)",
    )
    args = parser.parse_args()
    game_date = date.fromisoformat(args.date) if args.date else None

    if args.sport == "nba":
        result = backfill_nba_clv(game_date, dry_run=args.dry_run)
        summary = summarize_nba_clv(days=30)
    elif args.sport == "ufc":
        result = backfill_ufc_clv(game_date, dry_run=args.dry_run)
        summary = summarize_ufc_clv(days=30)
    else:
        if args.dry_run:
            print("Note: --dry-run applies to NBA only; MLB backfill always writes.")
        result = backfill_mlb_clv(game_date)
        summary = summarize_mlb_clv(days=30)

    print("Backfill:", result)
    print("Summary:", summary)

    if args.props and args.sport == "mlb":
        prop_result = backfill_prop_results(game_date)
        prop_summary = summarize_prop_tracker(days=30)
        print("Prop backfill:", prop_result)
        print("Prop tracker:", prop_summary)


if __name__ == "__main__":
    main()
