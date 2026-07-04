"""Import free UFC holdout moneylines CSV. Run from project root."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.ufc_odds_free import ODDS_2024_CSV, import_csv, load_csv_odds


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import free UFC moneyline CSV for 2024 holdout market eval. "
            "Expected columns: date, home_team, away_team, home_ml, away_ml."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="Path to source CSV (default: show help if missing)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=ODDS_2024_CSV,
        help=f"Output path (default {ODDS_2024_CSV})",
    )
    args = parser.parse_args()
    if args.source is None:
        if ODDS_2024_CSV.exists():
            df = load_csv_odds()
            print(f"Existing {ODDS_2024_CSV}: {len(df)} rows")
            return
        parser.error("source CSV required when no canonical file exists")
    dest = import_csv(args.source, args.dest)
    df = load_csv_odds(dest)
    print(f"Imported {len(df)} rows -> {dest}")


if __name__ == "__main__":
    main()
