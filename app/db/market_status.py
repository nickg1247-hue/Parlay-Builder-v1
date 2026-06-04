import json
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

METRICS_PATH = PROJECT_ROOT / "data" / "processed" / "mlb_market_metrics.json"


def get_market_eval_status() -> dict[str, Any]:
    if not METRICS_PATH.exists():
        return {
            "market_eval_status": "not_run",
            "market_matched_games": None,
            "market_match_rate_pct": None,
        }
    data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    return {
        "market_eval_status": "ok",
        "market_matched_games": data.get("matched_games"),
        "market_match_rate_pct": data.get("match_rate_pct"),
        "market_paper_roi": data.get("paper_trade_roi"),
    }
