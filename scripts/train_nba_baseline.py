"""Train NBA baseline home_win model. Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.nba_baseline import format_metrics_table, run_training

if __name__ == "__main__":
    results = run_training()
    print(format_metrics_table(results))
    gate = results.get("phase_gate", {})
    print(f"\nProduction gate passes: {gate.get('passes')}")
    print(f"Best naive log loss: {gate.get('best_naive_log_loss'):.4f}")
    print(f"Market proxy log loss: {gate.get('market_proxy_log_loss'):.4f}")
    print(f"Production model: {results.get('production_model')}")
    print(f"Metrics written to data/processed/nba_baseline_metrics.json")
