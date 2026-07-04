"""CLI wrapper for UFC fight ingest."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.ufc import run_ingest


def main() -> None:
    df = run_ingest()
    print(f"Ingested {len(df)} UFC fights -> data/processed/ufc_fights.parquet")


if __name__ == "__main__":
    main()
