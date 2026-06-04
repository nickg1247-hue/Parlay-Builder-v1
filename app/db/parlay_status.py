import json
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

PARLAYS_PATH = PROJECT_ROOT / "data" / "processed" / "mlb_parlays_today.json"


def get_parlay_status() -> dict[str, Any]:
    if not PARLAYS_PATH.exists():
        return {"parlay_status": "not_run", "parlay_count": None, "parlay_date": None}
    data = json.loads(PARLAYS_PATH.read_text(encoding="utf-8"))
    if data.get("error"):
        return {
            "parlay_status": "error",
            "parlay_count": 0,
            "parlay_date": data.get("date"),
        }
    return {
        "parlay_status": "ok",
        "parlay_count": len(data.get("parlays", [])),
        "parlay_date": data.get("date"),
    }
