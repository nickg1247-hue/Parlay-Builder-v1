"""One-shot NBA setup: ingest games + train ML, spread, and totals models."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.nba import run_ingest
from app.models.nba_baseline import MODEL_ARTIFACT, run_training as train_baseline
from app.models.nba_margin import MODEL_ARTIFACT as MARGIN_ARTIFACT, run_training as train_margin
from app.models.nba_totals import MODEL_ARTIFACT as TOTALS_ARTIFACT, run_training as train_totals


def main() -> None:
    print("Step 1/4: Ingest NBA games (stats.nba.com, no API key)...")
    run_ingest()
    print("Step 2/4: Train baseline moneyline model...")
    ml = train_baseline()
    print(f"  ML model: {ml.get('production_model')} -> {MODEL_ARTIFACT}")
    print("Step 3/4: Train margin / spread model...")
    margin = train_margin()
    print(f"  Spread gate: {margin.get('margin_production_gate_passes')} -> {MARGIN_ARTIFACT}")
    print("Step 4/4: Train totals (O/U points) model...")
    totals = train_totals()
    print(f"  Totals gate: {totals.get('totals_production_gate_passes')} -> {TOTALS_ARTIFACT}")
    print("Done. Open /nba/board -> Demo for model ML + margin + O/U columns.")


if __name__ == "__main__":
    main()
