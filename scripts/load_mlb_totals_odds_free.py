"""Load free historical MLB O/U lines from SBR dataset (same JSON as moneylines)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.odds.mlb_odds_free import load_or_build_2025_totals_csv

if __name__ == "__main__":
    df = load_or_build_2025_totals_csv()
    print(f"Totals rows: {len(df)}")
