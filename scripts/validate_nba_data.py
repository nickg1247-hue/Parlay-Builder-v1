"""Validate NBA modeling table in SQLite and processed files."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.nba_schema import NBA_GAMES_COLUMNS

PARQUET = PROJECT_ROOT / "data" / "processed" / "nba_games.parquet"
SEASON_LABELS = {2024: "2023-24", 2025: "2024-25", 2026: "2025-26"}


def main() -> None:
    conn = get_connection()
    try:
        df = pd.read_sql("SELECT * FROM nba_games", conn)
    finally:
        conn.close()

    if df.empty:
        print("nba_games table is empty. Run: python scripts/ingest_nba.py")
        sys.exit(1)

    dupes = int(df["game_id"].duplicated().sum())
    print(f"Row count: {len(df)}")
    print(f"Date range: {df['date'].min()} .. {df['date'].max()}")
    print(f"Duplicate game_id: {dupes}")
    print("\nRows by season (regular / playoff):")
    for season in sorted(df["season"].unique()):
        sub = df[df["season"] == season]
        label = SEASON_LABELS.get(int(season), str(season))
        regular = int((sub["game_type"] == "regular").sum())
        playoff = int((sub["game_type"] == "playoff").sum())
        print(f"  {season} ({label}): {regular} regular, {playoff} playoff, {len(sub)} total")

    print("\nNull counts:")
    null_fail = False
    for col in NBA_GAMES_COLUMNS:
        nulls = int(df[col].isna().sum())
        if nulls:
            null_fail = True
            print(f"  {col}: {nulls}")

    if PARQUET.exists():
        print(f"\nParquet file: {PARQUET} ({PARQUET.stat().st_size} bytes)")

    if dupes != 0 or null_fail:
        sys.exit(1)
    print("\nValidation passed.")


if __name__ == "__main__":
    main()
