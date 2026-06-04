"""Validate MLB modeling table in SQLite and processed files."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.mlb_schema import MLB_GAMES_COLUMNS

PARQUET = PROJECT_ROOT / "data" / "processed" / "mlb_games.parquet"


def main() -> None:
    conn = get_connection()
    try:
        df = pd.read_sql("SELECT * FROM mlb_games", conn)
    finally:
        conn.close()

    if df.empty:
        print("mlb_games table is empty. Run: python scripts/ingest_mlb.py")
        sys.exit(1)

    dupes = int(df["game_id"].duplicated().sum())
    print(f"Row count: {len(df)}")
    print(f"Date range: {df['date'].min()} .. {df['date'].max()}")
    print(f"Duplicate game_id: {dupes}")
    print("\nNull counts:")
    for col in MLB_GAMES_COLUMNS:
        nulls = int(df[col].isna().sum())
        if nulls:
            print(f"  {col}: {nulls}")

    if PARQUET.exists():
        print(f"\nParquet file: {PARQUET} ({PARQUET.stat().st_size} bytes)")

    if dupes != 0:
        sys.exit(1)
    print("\nValidation passed.")


if __name__ == "__main__":
    main()
