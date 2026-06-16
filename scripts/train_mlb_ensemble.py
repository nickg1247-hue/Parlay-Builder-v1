"""Train MLB moneyline ensemble (logistic + GBC + Elo). Run from project root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.mlb_ensemble import ENSEMBLE_METRICS_JSON, run_ensemble_training


def _fmt_holdout(label: str, metrics: dict) -> None:
    print(f"\n{label}")
    print(f"  Winner accuracy: {metrics.get('winner_accuracy')}")
    print(f"  Log loss:        {metrics.get('log_loss')}")
    print(f"  Brier:           {metrics.get('brier')}")
    print(f"  High-conf acc:   {metrics.get('high_confidence_accuracy')}")
    print(f"  +EV ROI:         {metrics.get('plus_ev_roi')}")
    print(f"  No-pick %:       {metrics.get('no_pick_pct')}")


if __name__ == "__main__":
    results = run_ensemble_training(promote=True)
    _fmt_holdout("Ensemble holdout (2025)", results["holdout"])
    _fmt_holdout("Logistic baseline holdout", results["baseline_logistic_holdout"])
    improved = results.get("promotion_improved_metrics") or []
    print(f"\nPromotion improved metrics vs logistic: {improved or 'none'}")
    print(f"Promoted to production: {results.get('promoted')}")
    print(f"Metrics written to {ENSEMBLE_METRICS_JSON.relative_to(ROOT)}")
