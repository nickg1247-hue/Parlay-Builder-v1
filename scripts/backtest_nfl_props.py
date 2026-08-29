#!/usr/bin/env python3
"""Grade cached NFL player props vs box scores. Does not retune the model."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.nfl_props_backtest import run_nfl_props_backtest  # noqa: E402
from app.services.slate_clock import slate_today  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OOS backtest of the current NFL prop scorer against ESPN box scores."
    )
    parser.add_argument(
        "--as-of",
        help="ISO date cutoff (default: ET slate today). Games on/after this date are skipped.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print JSON only; do not write data/processed/nfl_props_backtest.json",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else slate_today()
    report = run_nfl_props_backtest(as_of=as_of, write_report=not args.no_write)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    if report.get("n_decided"):
        print(
            f"n={report['n_decided']} hit_rate={report['hit_rate']} "
            f"log_loss={report['mean_log_loss']} paper_u={report['paper_units']}"
        )
    else:
        print(report.get("note") or "No graded NFL props.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
