"""Out-of-sample grading of cached NFL player props. Does not change scoring."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.services.nfl_player_stats import nfl_stat_on_date
from app.services.prop_pick_tracker import grade_prop_result
from app.services.props_nfl import NFL_PROPS_DIR
from app.services.slate_clock import slate_today

REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "nfl_props_backtest.json"


def _american_profit(odds: int | None, stake: float = 1.0) -> float:
    if odds is None:
        return 0.0
    n = int(odds)
    if n > 0:
        return stake * n / 100.0
    if n < 0:
        return stake * 100.0 / abs(n)
    return 0.0


def _log_loss(prob: float | None, hit: bool) -> float | None:
    if prob is None:
        return None
    p = min(0.999, max(0.001, float(prob)))
    return -math.log(p if hit else (1.0 - p))


def iter_cached_nfl_prop_files(root: Path | None = None) -> list[Path]:
    base = root or NFL_PROPS_DIR
    if not base.exists():
        return []
    return sorted(p for p in base.glob("*/*.json") if p.parent.name != "events")


def _best_side_rows(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One recommended side per player/market/line (highest prop_score)."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for prop in props:
        player = prop.get("player")
        market = prop.get("market_type")
        side = prop.get("recommended_side") or prop.get("side")
        if not player or not market or not side:
            continue
        try:
            line = float(prop.get("line") or 0)
        except (TypeError, ValueError):
            continue
        key = (prop.get("game_id"), player, market, line)
        score = float(prop.get("prop_score") or prop.get("score") or 0)
        prior = best.get(key)
        if prior is None or score > float(prior.get("prop_score") or prior.get("score") or 0):
            best[key] = {**prop, "recommended_side": side}
    return list(best.values())


def run_nfl_props_backtest(
    *,
    as_of: date | None = None,
    stat_fn: Callable[..., float | None] | None = None,
    cache_dir: Path | None = None,
    write_report: bool = False,
) -> dict[str, Any]:
    """Grade cached NFL recommendations vs box scores. No formula changes."""
    cutoff = as_of or slate_today()
    getter = stat_fn or nfl_stat_on_date
    files = iter_cached_nfl_prop_files(cache_dir)
    graded: list[dict[str, Any]] = []
    skipped_live = 0
    missing_stat = 0
    cache_files = 0

    for path in files:
        try:
            board_day = date.fromisoformat(path.parent.name)
        except ValueError:
            continue
        if board_day >= cutoff:
            skipped_live += 1
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        props = list(payload.get("props") or [])
        if not props:
            continue
        cache_files += 1
        for prop in _best_side_rows(props):
            actual = getter(
                str(prop.get("player") or ""),
                str(prop.get("market_type") or ""),
                board_day,
                team_abbr=prop.get("team"),
            )
            if actual is None:
                missing_stat += 1
                continue
            side = str(prop.get("recommended_side") or "")
            line = float(prop.get("line") or 0)
            hit, status = grade_prop_result(float(actual), line, side)
            model_p = prop.get("model_probability")
            ll = _log_loss(model_p, bool(hit)) if hit is not None else None
            pnl = 0.0
            if status == "settled" and hit is True:
                pnl = _american_profit(prop.get("recommended_odds"))
            elif status == "settled" and hit is False:
                pnl = -1.0
            graded.append(
                {
                    "board_date": board_day.isoformat(),
                    "player": prop.get("player"),
                    "market_type": prop.get("market_type"),
                    "side": side,
                    "line": line,
                    "actual": round(float(actual), 3),
                    "hit": hit,
                    "result_status": status,
                    "model_probability": model_p,
                    "market_probability": prop.get("market_probability"),
                    "edge": prop.get("edge"),
                    "log_loss": None if ll is None else round(ll, 4),
                    "paper_units": round(pnl, 4),
                    "plus_ev": bool(
                        model_p is not None
                        and prop.get("market_probability") is not None
                        and float(model_p) > float(prop.get("market_probability") or 0)
                    ),
                }
            )

    decided = [r for r in graded if r["result_status"] == "settled" and r["hit"] is not None]
    hits = sum(1 for r in decided if r["hit"] is True)
    misses = sum(1 for r in decided if r["hit"] is False)
    ll_vals = [r["log_loss"] for r in decided if r.get("log_loss") is not None]
    plus_ev = [r for r in decided if r.get("plus_ev")]
    plus_ev_hits = sum(1 for r in plus_ev if r["hit"] is True)
    plus_ev_misses = sum(1 for r in plus_ev if r["hit"] is False)

    report = {
        "as_of": cutoff.isoformat(),
        "formula_changed": False,
        "cache_files_used": cache_files,
        "cache_files_skipped_live_or_today": skipped_live,
        "n_graded": len(graded),
        "n_decided": len(decided),
        "n_missing_box_stat": missing_stat,
        "hit_rate": round(hits / (hits + misses), 4) if (hits + misses) else None,
        "mean_log_loss": round(sum(ll_vals) / len(ll_vals), 4) if ll_vals else None,
        "paper_units": round(sum(r["paper_units"] for r in graded), 4),
        "plus_ev_n": len(plus_ev),
        "plus_ev_hit_rate": (
            round(plus_ev_hits / (plus_ev_hits + plus_ev_misses), 4)
            if (plus_ev_hits + plus_ev_misses)
            else None
        ),
        "plus_ev_paper_units": round(sum(r["paper_units"] for r in plus_ev), 4),
        "empty_reason": None if files else "no_cache",
        "note": (
            "No NFL prop cache files under data/processed/props_repository/nfl/. "
            "Run python scripts/refresh_props_slate.py --sport nfl then re-run this backtest."
            if not files
            else "Cached lines only; scoring weights were not modified."
        ),
        "rows": graded[:500],
    }
    if write_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(REPORT_PATH)
    return report
