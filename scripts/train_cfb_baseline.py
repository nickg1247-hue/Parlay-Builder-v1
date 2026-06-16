"""Train CFB v1 moneyline baseline (logistic + Platt)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.cfb_baseline import METRICS_JSON, MODEL_ARTIFACT, format_metrics_table, run_training


def main() -> None:
    results = run_training()
    print(format_metrics_table(results))
    holdout = results.get("active_holdout", {})
    gate = results.get("phase_gate", {})
    print(f"\nActive model: {results.get('active_model')} ({results.get('feature_set')})")
    print(f"Holdout log loss: {holdout.get('log_loss')}")
    print(f"Promoted v2: {results.get('promoted_v2')} · Promoted v3: {results.get('promoted_v3')}")
    print(f"Gate passes: {gate.get('passes')}")
    print(f"Artifact: {MODEL_ARTIFACT}")
    print(f"Metrics: {METRICS_JSON}")


if __name__ == "__main__":
    main()
