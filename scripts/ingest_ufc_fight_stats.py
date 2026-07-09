"""CLI wrapper for UFC per-bout fight stats ingest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.ufc_fight_stats import run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest UFC fight stats from ESPN")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max fights to fetch (default: all missing)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch fights even if already in parquet",
    )
    args = parser.parse_args()
    df = run_ingest(limit=args.limit, skip_existing=not args.refresh)
    print(f"UFC fight stats: {len(df)} rows -> data/processed/ufc_fight_stats.parquet")


if __name__ == "__main__":
    main()
