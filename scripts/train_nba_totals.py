"""Train NBA totals (O/U points) model. Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.nba_totals import run_training

if __name__ == "__main__":
    results = run_training()
    print("\n=== NBA totals training ===")
    print(f"Holdout MAE (model): {results['holdout_mae_total_pts']}")
    print(f"Holdout MAE (league avg): {results['league_avg_mae_total_pts']}")
    print(f"Log loss model: {results['log_loss_model']}")
    print(f"Log loss market: {results['log_loss_market']}")
    print(f"Totals production gate passes: {results['totals_production_gate_passes']}")
    print(f"Board totals enabled: {results['board_totals_enabled']}")
    print("Artifacts:")
    print("  data/processed/nba_totals_model.joblib")
    print("  data/processed/nba_totals_metrics.json")
    print("  data/processed/active_nba_totals_model.json")
    print("See TOTALS_NBA.md for gate rules and Normal vs Poisson choice.")
