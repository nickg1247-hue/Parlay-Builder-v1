"""One-shot UFC setup: ingest fights + train moneyline model."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.ufc import run_ingest
from app.models.ufc_baseline import METRICS_JSON, MODEL_ARTIFACT, run_training
from app.odds.ufc_odds_free import ODDS_2024_CSV, import_csv

FIXTURE_ODDS = ROOT / "data" / "fixtures" / "ufc_odds_2024_demo.csv"


def main() -> None:
    print("Step 1/3: Ingest UFC fights (ESPN MMA API)...")
    df = run_ingest()
    print(f"  Ingested {len(df)} completed fights")
    print("Step 2/3: Train baseline moneyline model...")
    ml = run_training()
    holdout = ml.get("active_holdout", {})
    print(f"  Model: {ml.get('production_model')} -> {MODEL_ARTIFACT}")
    print(f"  Holdout log loss: {holdout.get('log_loss', 'n/a')}")
    print(f"  Metrics: {METRICS_JSON}")
    if not ODDS_2024_CSV.exists() and FIXTURE_ODDS.exists():
        print("Step 3/3: Import demo holdout odds (2024-01-13 card)...")
        import_csv(FIXTURE_ODDS)
        print(f"  Demo odds → {ODDS_2024_CSV}")
    else:
        print("Step 3/3: Holdout odds CSV already present (skip demo import).")
    print("Done. Open /ufc for the next card, /ufc/board Demo, or /ufc/game/{id}?date=2024-01-13.")


if __name__ == "__main__":
    main()
