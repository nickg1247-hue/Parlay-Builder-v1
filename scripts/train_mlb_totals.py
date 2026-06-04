"""Train MLB totals (Over/Under) model."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.mlb_totals import run_training

if __name__ == "__main__":
    results = run_training()
    m = results["metrics"]
    print("| Model | MAE | O/U log loss | Edge hit rate |")
    print("|-------|-----|--------------|---------------|")
    for name, row in m.items():
        ll = row.get("log_loss")
        hr = row.get("hit_rate_edge_flagged")
        mae = row.get("mae")
        print(
            f"| {name} | {mae if mae is not None else '—':} | "
            f"{ll if ll is not None else '—':} | {hr if hr is not None else '—':} |"
        )
    print(f"\nProduction gate passes: {results['phase_gate']['passes']}")
