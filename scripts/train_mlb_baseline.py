"""Train MLB baseline home_win model. Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.mlb_baseline import format_metrics_table, run_training

if __name__ == "__main__":
    results = run_training()
    print(format_metrics_table(results))
    gate = results["phase_gate"]
    passed = gate["beats_home_baseline_log_loss"] and gate["beats_elo_baseline_log_loss"]
    print(f"\nPhase gate (log loss vs both baselines): {'PASS' if passed else 'FAIL'}")
