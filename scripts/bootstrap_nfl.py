"""One-shot NFL setup: ESPN ingest + moneyline + spread + totals."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.nfl import run_ingest
from app.models.nfl_baseline import METRICS_JSON, MODEL_ARTIFACT, run_training
from app.models.nfl_margin import run_training as run_margin_training
from app.models.nfl_totals import run_training as run_totals_training


def main() -> None:
    print("Step 1/4: Ingest NFL games (ESPN preseason + regular, no API key)...")
    df = run_ingest()
    print(f"  Games: {len(df)} -> data/processed/nfl_games.parquet")
    if "game_type" in df.columns:
        print(f"  Preseason: {int((df['game_type'] == 'preseason').sum())}")
        print(f"  Regular: {int((df['game_type'] == 'regular').sum())}")

    print("Step 2/4: Train baseline moneyline model...")
    ml = run_training()
    holdout = ml.get("active_holdout", {})
    gate = ml.get("phase_gate", {})
    print(f"  ML model: {ml.get('production_model')} -> {MODEL_ARTIFACT}")
    print(f"  Holdout log loss: {holdout.get('log_loss', 'n/a')}")
    print(f"  Gate passes: {gate.get('passes')}")
    print(f"  Metrics: {METRICS_JSON}")

    print("Step 3/4: Train spread / margin model...")
    margin = run_margin_training()
    print(f"  Holdout MAE: {margin.get('holdout_mae_margin')}")
    print(f"  Gate passes: {margin.get('margin_production_gate_passes')}")

    print("Step 4/4: Train totals (O/U) model...")
    totals = run_totals_training()
    print(f"  Holdout MAE: {totals.get('holdout_mae_total_pts')}")
    print(f"  Gate passes: {totals.get('totals_production_gate_passes')}")
    print("Done. Open /nfl for the weekly slate and /nfl/board for +EV / parlays.")


if __name__ == "__main__":
    main()
