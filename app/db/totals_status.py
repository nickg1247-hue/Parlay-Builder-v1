"""Totals model status for health endpoint."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.models.mlb_totals import METRICS_JSON, MODEL_ARTIFACT
from app.odds.mlb_odds_free import TOTALS_2025_CSV

BACKTEST_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_totals_backtest_recent.json"


def get_totals_model_status() -> dict:
    status = {
        "totals_model_loaded": MODEL_ARTIFACT.exists(),
        "totals_model_version": None,
        "totals_holdout_log_loss": None,
        "totals_market_log_loss": None,
        "totals_gate_passes": None,
        "totals_2025_lines_rows": 0,
        "totals_backtest_days": None,
        "totals_backtest_hit_rate": None,
    }
    if TOTALS_2025_CSV.exists():
        import pandas as pd

        status["totals_2025_lines_rows"] = len(pd.read_csv(TOTALS_2025_CSV))
    if METRICS_JSON.exists():
        m = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
        status["totals_model_version"] = m.get("production_model")
        gbr = m.get("metrics", {}).get("gbr_totals", {})
        mkt = m.get("metrics", {}).get("market_implied", {})
        status["totals_holdout_log_loss"] = gbr.get("log_loss")
        status["totals_market_log_loss"] = mkt.get("log_loss")
        status["totals_gate_passes"] = m.get("phase_gate", {}).get("passes")
    if BACKTEST_JSON.exists():
        b = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
        status["totals_backtest_days"] = b.get("days")
        status["totals_backtest_hit_rate"] = b.get("hit_rate_edge_flagged")
    return status
