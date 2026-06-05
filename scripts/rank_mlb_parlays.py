"""Rank today's cross-game MLB parlays by EV. Run from project root."""

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import run_parlay_ranker_with_message


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank MLB cross-game parlays by EV")
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_EDGE,
        help=f"Min parlay EV (default {DEFAULT_MIN_EDGE})",
    )
    parser.add_argument("--max-parlays", type=int, default=5, help="Max parlays to show")
    parser.add_argument("--min-legs", type=int, default=2)
    parser.add_argument("--max-legs", type=int, default=4)
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Use historical odds CSV when API key missing or for replay",
    )
    args = parser.parse_args()

    game_date = date.fromisoformat(args.date) if args.date else date.today()
    print(run_parlay_ranker_with_message(
        game_date=game_date,
        min_edge=args.min_edge,
        max_parlays=args.max_parlays,
        min_legs=args.min_legs,
        max_legs=args.max_legs,
        use_cache=args.use_cache,
    ))


if __name__ == "__main__":
    main()
