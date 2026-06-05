"""Load free historical MLB O/U lines from SBR dataset (same JSON as moneylines)."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.mlb_odds_free import LAB_TOTALS_SEASONS, load_or_build_season_totals_csv

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--season",
        type=int,
        default=2025,
        choices=LAB_TOTALS_SEASONS,
        help="Season to parse from cached JSON (2024 for Lab validation, 2025 for demo)",
    )
    parser.add_argument("--force", action="store_true", help="Re-parse JSON into CSV")
    args = parser.parse_args()
    df = load_or_build_season_totals_csv(args.season, force_parse=args.force)
    print(f"Totals rows ({args.season}): {len(df)}")
