"""Self-learning: store predictions and outcomes for model audit."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

PREDICTIONS_PATH = PROJECT_ROOT / "data" / "processed" / "prop_model_predictions.jsonl"


def log_model_prediction(
    prop: dict[str, Any],
    *,
    board_date: date | str,
    source: str = "daily_scan",
) -> None:
    """Append one scored prop snapshot for post-game learning."""
    if not prop.get("actionable"):
        return
    row = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "board_date": str(board_date)[:10],
        "source": source,
        "player": prop.get("player") or prop.get("player_name"),
        "game_id": prop.get("game_id"),
        "market_type": prop.get("market_type") or prop.get("stat_type"),
        "line": prop.get("line"),
        "recommended_side": prop.get("recommended_side"),
        "prop_score": prop.get("prop_score") or prop.get("score"),
        "confidence_tier": prop.get("confidence_tier") or prop.get("confidence"),
        "model_projection": prop.get("model_projection"),
        "model_probability": prop.get("recommended_probability"),
        "market_probability": (
            prop.get("market_probability_over")
            if prop.get("recommended_side") == "over"
            else prop.get("market_probability_under")
        ),
        "edge_pct": prop.get("edge_pct"),
        "component_scores": prop.get("component_scores"),
        "american_odds": prop.get("recommended_odds"),
        "actual_stat": None,
        "hit": None,
        "projection_error": None,
        "result_status": "pending",
    }
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PREDICTIONS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def backfill_prediction_result(
    *,
    player: str,
    market_type: str,
    board_date: str,
    line: float,
    side: str,
    actual_stat: float | None,
    hit: bool | None,
    projection: float | None,
) -> None:
    """Update pending prediction rows when results settle (best-effort rewrite)."""
    if not PREDICTIONS_PATH.exists():
        return
    lines = PREDICTIONS_PATH.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        row = json.loads(raw)
        if (
            row.get("result_status") == "pending"
            and row.get("board_date") == board_date
            and row.get("player") == player
            and row.get("market_type") == market_type
            and float(row.get("line", -1)) == float(line)
            and row.get("recommended_side") == side
        ):
            row["actual_stat"] = actual_stat
            row["hit"] = hit
            row["projection_error"] = (
                round(actual_stat - projection, 3)
                if actual_stat is not None and projection is not None
                else None
            )
            row["result_status"] = "settled" if hit is not None else "push"
        updated.append(json.dumps(row, separators=(",", ":")))
    PREDICTIONS_PATH.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
