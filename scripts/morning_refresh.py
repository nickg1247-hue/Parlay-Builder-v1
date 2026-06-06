"""Morning MLB board + schedule refresh (run via Task Scheduler or manually)."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.morning_refresh import run_morning_refresh


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Pre-build daily board and schedule caches"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Board date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--sports",
        type=str,
        default="mlb",
        help="Comma-separated sports to refresh (default: mlb). Example: mlb,nba",
    )
    args = parser.parse_args()
    game_date = date.fromisoformat(args.date) if args.date else None
    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    sys.exit(run_morning_refresh(game_date, sports=sports))


if __name__ == "__main__":
    main()
