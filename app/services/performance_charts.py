"""Chart series for model vs market and prop-tracker performance."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.odds_math import american_payout_profit
from app.services.daily_board import DAILY_BOARD_CACHE
from app.services.prop_pick_tracker import _latest_by_pick_id, _read_all_rows


def _load_board_slate() -> list[dict[str, Any]]:
    if not DAILY_BOARD_CACHE.exists():
        return []
    try:
        payload = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return payload.get("slate") or []


def model_vs_market_chart(limit: int = 12) -> dict[str, Any]:
    """Today's slate: model vs market implied prob on the model pick side."""
    points: list[dict[str, Any]] = []
    for row in _load_board_slate():
        if len(points) >= limit:
            break
        model_home = row.get("model_prob_home")
        market_home = row.get("market_prob_home")
        if model_home is None or market_home is None:
            continue
        side = row.get("model_pick_side") or row.get("best_pick", {}).get("side")
        if side == "away":
            model_p = round(1.0 - float(model_home), 4)
            market_p = round(1.0 - float(market_home), 4)
        else:
            model_p = round(float(model_home), 4)
            market_p = round(float(market_home), 4)
        label = row.get("matchup") or row.get("away_team") or "Game"
        if len(label) > 22:
            label = label[:20] + "…"
        points.append(
            {
                "label": label,
                "model_pct": round(model_p * 100, 1),
                "market_pct": round(market_p * 100, 1),
                "edge_pct": round((model_p - market_p) * 100, 1),
            }
        )
    return {"points": points, "count": len(points)}


def performance_trend_chart(days: int = 30) -> dict[str, Any]:
    """Cumulative hit rate and ROI from settled prop tracker picks."""
    cutoff = datetime.now(timezone.utc).date()
    min_date = cutoff - timedelta(days=days) if days > 0 else None
    rows = [
        r
        for r in _latest_by_pick_id(_read_all_rows()).values()
        if r.get("result_status") == "settled" and r.get("board_date")
    ]
    if min_date:
        rows = [
            r
            for r in rows
            if date.fromisoformat(str(r["board_date"])) >= min_date
        ]
    rows.sort(key=lambda r: (str(r.get("board_date") or ""), str(r.get("logged_at") or "")))

    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        d = str(row.get("board_date") or "")
        by_date.setdefault(d, []).append(row)

    series: list[dict[str, Any]] = []
    hits = 0
    decided = 0
    profit = 0.0
    bets = 0.0

    for day in sorted(by_date.keys()):
        for row in by_date[day]:
            hit = row.get("hit")
            if hit is None:
                continue
            decided += 1
            if hit:
                hits += 1
            odds = row.get("american_odds_at_offer")
            if odds is not None:
                bets += 10.0
                profit += american_payout_profit(int(odds), bool(hit))
        hit_rate = round(hits / decided, 4) if decided else None
        roi = round(profit / bets, 4) if bets else None
        series.append(
            {
                "date": day,
                "hit_rate_pct": round(hit_rate * 100, 1) if hit_rate is not None else None,
                "roi_pct": round(roi * 100, 1) if roi is not None else None,
                "settled": decided,
            }
        )

    overall_hr = round(hits / decided, 4) if decided else None
    overall_roi = round(profit / bets, 4) if bets else None
    return {
        "days": days,
        "series": series,
        "overall_hit_rate_pct": round(overall_hr * 100, 1) if overall_hr is not None else None,
        "overall_roi_pct": round(overall_roi * 100, 1) if overall_roi is not None else None,
        "settled": decided,
    }
