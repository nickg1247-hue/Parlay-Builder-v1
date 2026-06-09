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
    v1 = results.get("v1_comparison", {}).get("holdout", {})
    v2 = results.get("v2_comparison", {}).get("holdout", {})
    print(f"\nFeature set (v2 trained): {results.get('feature_set')}")
    print(f"v1 holdout log loss: {v1.get('log_loss', 0):.4f}")
    print(f"v2 holdout log loss: {v2.get('log_loss', 0):.4f}")
    print(f"Promoted v2: {results.get('promoted_v2')}")
    print(f"Production gate passes: {gate.get('passes')}")
    print(f"Best naive log loss: {gate.get('best_naive_log_loss'):.4f}")
    print(f"Market proxy log loss: {gate.get('market_proxy_log_loss'):.4f}")
    print(f"Production model: {results.get('production_model')}")
    print(f"Metrics written to data/processed/nba_baseline_metrics.json")
