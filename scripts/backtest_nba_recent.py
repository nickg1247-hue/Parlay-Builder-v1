"""Rolling-window NBA backtest — writes nba_backtest_report.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.nba_backtest_report import (
    DEFAULT_END,
    DEFAULT_START,
    run_nba_backtest_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NBA rolling holdout backtest (model + optional market paper trade)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Last N days from latest completed holdout game (mutually exclusive with --start/--end)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Window start YYYY-MM-DD (use with --end)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Window end YYYY-MM-DD (use with --start)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=None,
        help="+EV edge threshold (default: DEFAULT_MIN_EDGE 0.08)",
    )
    args = parser.parse_args()

    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be used together")

    kwargs: dict = {"write_cache": True}
    if args.min_edge is not None:
        kwargs["min_edge"] = args.min_edge

    if args.start and args.end:
        kwargs["start_date"] = date.fromisoformat(args.start)
        kwargs["end_date"] = date.fromisoformat(args.end)
    elif args.days is not None:
        kwargs["days"] = args.days
    else:
        kwargs["start_date"] = DEFAULT_START
        kwargs["end_date"] = DEFAULT_END

    result = run_nba_backtest_report(**kwargs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
