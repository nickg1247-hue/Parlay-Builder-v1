"""Run UFC walk-forward backtest and print summary."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ufc_backtest_report import run_ufc_walk_forward_backtest


def main() -> None:
    report = run_ufc_walk_forward_backtest(write_cache=True)
    agg = report.get("aggregate") or {}
    proof = report.get("proof_summary") or {}
    print("Status:", report.get("status"))
    print("Holdout fights:", agg.get("holdout_fights_scored"))
    print("Moneyline accuracy:", agg.get("moneyline_accuracy_pct"), "%")
    print("Moneyline log loss:", agg.get("moneyline_log_loss"))
    print("Beats naive every fold:", agg.get("beats_naive_every_fold"))
    print("Verdict:", proof.get("verdict"))
    print("Report:", report.get("report_path"))
    if report.get("folds"):
        print("\nPer-season folds:")
        for fold in report["folds"]:
            ml = fold["moneyline"]
            print(
                f"  {fold['holdout_season']}: "
                f"{ml['accuracy_pct']}% acc, LL {ml['log_loss']}, "
                f"beats_naive={ml['beats_naive']}"
            )
    print(json.dumps(report.get("feature_effects", {}).get("logistic_importance_avg", [])[:5], indent=2))


if __name__ == "__main__":
    main()
