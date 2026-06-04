"""Download/load free 2025 MLB moneylines. Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse

from app.odds.mlb_odds_free import load_or_build_2025_csv

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-parse cached JSON into mlb_odds_2025.csv",
    )
    args = parser.parse_args()
    df = load_or_build_2025_csv(force_download=args.force, force_parse=args.force)
    print(f"2025 odds rows: {len(df)}")
