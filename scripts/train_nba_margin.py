"""Train NBA margin / spread cover model. Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.nba_margin import format_metrics_table, run_training

if __name__ == "__main__":
    results = run_training()
    print(format_metrics_table(results))
    print(f"\nMargin production gate passes: {results['margin_production_gate_passes']}")
    print(f"Board spread enabled: {results['board_spread_enabled']}")
    print(f"Beats v2 logistic: {results['beats_v2_logistic']}")
    print(f"Moneyline promotion eligible: {results['moneyline_promotion_eligible']}")
    print("Metrics written to data/processed/nba_margin_metrics.json")
