"""Train CFB v1 margin / spread model (GBR + Normal cover)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.cfb_margin import METRICS_JSON, MODEL_ARTIFACT, run_training


def main() -> None:
    results = run_training()
    print(f"Holdout MAE (margin): {results['holdout_mae_margin']}")
    print(f"Proxy cover log loss (home @ {results['proxy_lines']['home_spread_point']}): "
          f"{results['proxy_cover_log_loss_home']}")
    print(f"Margin production gate: {results['margin_production_gate_passes']}")
    print(f"Artifact: {MODEL_ARTIFACT}")
    print(f"Metrics: {METRICS_JSON}")


if __name__ == "__main__":
    main()
