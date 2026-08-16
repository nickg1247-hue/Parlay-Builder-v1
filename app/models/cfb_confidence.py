"""Empirical CFB pick categories: toss-up, soft, hard, lock.

Same fitter as NFL, but cuts are fit on CFB walk-forward favorites.
College favorites are larger and lose more — do not reuse NFL cuts.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import PROJECT_ROOT
from app.models.nfl_confidence import (
    FLOORS,
    MIN_GAMES,
    _smallest_lo,
    _toss_hi_from_bins,
    apply_categories,
    assign_category,
    category_label,
    category_proof,
    fit_category_cuts,
    labeled_proof,
)

CUTS_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_confidence_cuts.json"
DEFAULT_CUTS = {
    "cuts": {
        "toss-up": {"lo": 50.0, "hi": 58.0},
        "soft": {"lo": 58.0, "hi": 78.0},
        "hard": {"lo": 78.0, "hi": 92.0},
        "lock": None,
        "hard_tail": None,
    }
}


@lru_cache(maxsize=1)
def load_category_cuts() -> dict[str, Any]:
    if CUTS_JSON.exists():
        try:
            payload = json.loads(CUTS_JSON.read_text(encoding="utf-8"))
            if payload.get("cuts"):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CUTS)


def save_category_cuts(cuts: dict[str, Any]) -> None:
    CUTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CUTS_JSON.write_text(json.dumps(cuts, indent=2), encoding="utf-8")
    load_category_cuts.cache_clear()


def fit_cfb_category_cuts(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit CFB bands so hard/soft are measured against the lock cutoff, not 100%."""
    cuts = fit_category_cuts(games)
    table = cuts.get("cuts") or {}
    lock = table.get("lock")
    hard_hi = (
        float(lock["lo"])
        if lock and lock.get("lo") is not None
        else 100.01
    )
    rows = [g for g in games if g.get("home_pct") is not None]
    hard_lo = _smallest_lo(rows, hard_hi, FLOORS["hard"], MIN_GAMES["hard"])
    table["hard"] = {"lo": hard_lo, "hi": hard_hi} if hard_lo is not None else None
    soft_hi = hard_lo if hard_lo is not None else hard_hi
    toss_hi = _toss_hi_from_bins(rows)
    soft_lo = _smallest_lo(rows, soft_hi, FLOORS["soft"], MIN_GAMES["soft"])
    if soft_lo is not None and soft_lo < toss_hi:
        soft_lo = toss_hi
    table["soft"] = {"lo": soft_lo, "hi": soft_hi} if soft_lo is not None else None
    if soft_lo is not None:
        toss_hi = min(toss_hi, soft_lo)
    table["toss-up"] = {"lo": 50.0, "hi": toss_hi}
    if table.get("soft") is None and table.get("hard") and table.get("toss-up"):
        gap_lo = float(table["toss-up"]["hi"])
        gap_hi = float(table["hard"]["lo"])
        if gap_hi > gap_lo:
            table["soft"] = {"lo": gap_lo, "hi": gap_hi}
    cuts["cuts"] = table
    cuts["sport"] = "cfb"
    return cuts


def category_for_proba(home_prob: float, game_type: str | None = None) -> str:
    del game_type
    home_pct = float(home_prob) * 100.0
    return assign_category(home_pct, 100.0 - home_pct, load_category_cuts())


__all__ = [
    "CUTS_JSON",
    "apply_categories",
    "assign_category",
    "category_for_proba",
    "category_label",
    "category_proof",
    "fit_category_cuts",
    "fit_cfb_category_cuts",
    "labeled_proof",
    "load_category_cuts",
    "save_category_cuts",
]
