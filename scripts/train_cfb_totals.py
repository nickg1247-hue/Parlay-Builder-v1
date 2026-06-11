"""Train CFB v1 totals (O/U points) model (GBR + Normal over prob)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.cfb_totals import METRICS_JSON, MODEL_ARTIFACT, run_training


def main() -> None:
    results = run_training()
    print(f"Holdout MAE (total pts): {results['holdout_mae_total_pts']}")
    print(f"Proxy O/U line: {results['proxy_ou_line']}")
    print(f"Log loss model vs league: {results['log_loss_model']} / {results['log_loss_league_avg']}")
    print(f"Totals production gate: {results['totals_production_gate_passes']}")
    print(f"Artifact: {MODEL_ARTIFACT}")
    print(f"Metrics: {METRICS_JSON}")


if __name__ == "__main__":
    main()
