"""Backfill closing odds on forward CLV log rows (run afternoon/evening)."""

from __future__ import annotations

import argparse
from datetime import date

from app.services.forward_clv import backfill_closing_odds, summarize_clv


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill closing lines for forward CLV log")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Limit to board_date YYYY-MM-DD (default: all open rows)",
    )
    args = parser.parse_args()
    game_date = date.fromisoformat(args.date) if args.date else None
    result = backfill_closing_odds(game_date)
    print("Backfill:", result)
    print("Summary:", summarize_clv(days=30))


if __name__ == "__main__":
    main()
