"""Bulk-import UFC holdout moneylines from external CSV formats."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.ufc_odds_free import ODDS_2024_CSV, import_bulk, load_csv_odds

DEFAULT_FIXTURE = ROOT / "data" / "fixtures" / "ufc_odds_mikespa_master.csv"
DEMO_FIXTURE = ROOT / "data" / "fixtures" / "ufc_odds_2024_demo.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import one or more UFC odds CSVs (canonical, MikeSpa ufc-master, "
            "BestFightOdds export, favourite/underdog) into ufc_odds_2024.csv."
        )
    )
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        help="CSV paths (default: MikeSpa master + demo fixtures)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=ODDS_2024_CSV,
        help=f"Canonical output (default {ODDS_2024_CSV})",
    )
    parser.add_argument(
        "--with-demo",
        action="store_true",
        help="Also merge data/fixtures/ufc_odds_2024_demo.csv",
    )
    args = parser.parse_args()

    sources = list(args.sources)
    if not sources:
        if DEFAULT_FIXTURE.exists():
            sources.append(DEFAULT_FIXTURE)
        if args.with_demo and DEMO_FIXTURE.exists():
            sources.append(DEMO_FIXTURE)
    if not sources:
        parser.error("No source CSVs found. Pass paths or add ufc_odds_mikespa_master.csv fixture.")

    dest = import_bulk(sources, args.dest)
    df = load_csv_odds(dest)
    print(f"Imported {len(df)} rows -> {dest}")
    if not df.empty:
        years = sorted({str(d)[:4] for d in df["date"].unique()})
        print(f"Date years: {', '.join(years)}")


if __name__ == "__main__":
    main()
