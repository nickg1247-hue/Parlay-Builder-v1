"""Export 2024 holdout fights missing odds for manual BestFightOdds CSV fill."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from app.models.ufc_baseline import HOLDOUT_SEASON, load_fights
from app.odds.ufc_odds_free import load_holdout_odds
from app.odds.ufc_odds_match import merge_fights_odds_fuzzy

OUT = ROOT / "data" / "processed" / "ufc_odds_2024_gap.csv"


def main() -> None:
    fights = load_fights()
    holdout = fights[fights["season"] == HOLDOUT_SEASON].copy()
    dates = set(pd.to_datetime(holdout["date"]).dt.strftime("%Y-%m-%d"))
    odds = load_holdout_odds(dates)
    matched = merge_fights_odds_fuzzy(holdout, odds)
    matched_ids = set(matched.get("fight_id", pd.Series(dtype=str)).astype(str))
    gap = holdout[~holdout["fight_id"].astype(str).isin(matched_ids)].copy()
    gap["date"] = pd.to_datetime(gap["date"]).dt.strftime("%Y-%m-%d")
    out = gap[["date", "home_team", "away_team"]].copy()
    out["home_ml"] = ""
    out["away_ml"] = ""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    rate = 100.0 * len(matched) / len(holdout) if len(holdout) else 0.0
    print(f"Holdout {HOLDOUT_SEASON}: {len(holdout)} fights, {len(matched)} matched ({rate:.1f}%)")
    print(f"Gap template ({len(out)} rows) -> {OUT}")
    print("Fill home_ml/away_ml (American) and run: python scripts/load_ufc_odds_free.py <filled.csv>")


if __name__ == "__main__":
    main()
