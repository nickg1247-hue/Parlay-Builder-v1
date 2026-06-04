"""Rolling-window MLB backtest (moneyline + totals) — writes mlb_backtest_report.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.backtest_report import run_backtest_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    result = run_backtest_report(args.days)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
