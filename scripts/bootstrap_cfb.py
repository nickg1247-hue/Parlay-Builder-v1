"""One-shot CFB setup: ingest games + train ML, spread, and totals models."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.cfb import run_ingest
from app.models.cfb_baseline import METRICS_JSON as ML_METRICS, MODEL_ARTIFACT, run_training as train_baseline
from app.models.cfb_margin import METRICS_JSON as MARGIN_METRICS, MODEL_ARTIFACT as MARGIN_ARTIFACT, run_training as train_margin
from app.models.cfb_totals import METRICS_JSON as TOTALS_METRICS, MODEL_ARTIFACT as TOTALS_ARTIFACT, run_training as train_totals


def main() -> None:
    print("Step 1/5: Ingest CFB games (CFBD API, requires CFBD_API_KEY)...")
    run_ingest()
    print("Step 2/5: Cache ESPN FBS team logos...")
    from app.services.cfb_team_logos import refresh_cfb_logo_map

    logos = refresh_cfb_logo_map(force=True)
    print(f"  Logo lookup keys: {len(logos)}")
    print("Step 3/5: Train baseline moneyline model...")
    ml = train_baseline()
    holdout = ml.get("active_holdout", {})
    print(f"  ML model: {ml.get('production_model')} -> {MODEL_ARTIFACT}")
    print(f"  Holdout log loss: {holdout.get('log_loss', 'n/a')}")
    print("Step 4/5: Train margin / spread model...")
    margin = train_margin()
    print(f"  Spread gate: {margin.get('margin_production_gate_passes')} -> {MARGIN_ARTIFACT}")
    print("Step 5/5: Train totals (O/U points) model...")
    totals = train_totals()
    print(f"  Totals gate: {totals.get('totals_production_gate_passes')} -> {TOTALS_ARTIFACT}")
    print(f"  Metrics: {ML_METRICS}, {MARGIN_METRICS}, {TOTALS_METRICS}")
    print("Done. Open /cfb?date=20241130 for slate with ML, spread, and O/U lean chips.")


if __name__ == "__main__":
    main()
