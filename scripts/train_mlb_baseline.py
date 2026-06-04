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
    cal = results.get("calibration", {})
    fav = cal.get("favorite_pick_agreement", {})
    if fav.get("agreement_rate") is not None:
        print(
            f"\nFavorite agreement (market home >55%): "
            f"{fav['agreement_rate']:.1%} ({fav.get('n_model_agrees')}/{fav.get('n_market_home_favorite')})"
        )
    print(f"\nProduction model: {results.get('production_model')}")
    print(f"Replaced production artifact: {results.get('replaced_artifact')}")
    gate = results.get("phase_gate", {})
    print(f"Wave1 logistic beats market+v1: {gate.get('wave1_logistic_beats_market_and_v1')}")
    print(f"Wave1 GBC beats market+v1: {gate.get('wave1_gbc_beats_market_and_v1')}")
