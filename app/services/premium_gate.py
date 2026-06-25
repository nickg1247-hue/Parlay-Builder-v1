"""Server-side redaction of premium pick fields (free vs paid tiers)."""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from fastapi import Request

from app.auth.user_auth import get_user_session
from app.services.subscriptions import is_premium_user

_SLATE_PREMIUM_KEYS = frozenset({
    "ev_pick_team",
    "ev_pick_edge",
    "plus_ev_single",
    "plus_ev_total",
    "ml_confidence",
    "totals_confidence",
    "model_confidence",
    "spread_confidence",
    "spread_pick",
    "totals_pick",
    "expected_total_runs",
    "line_strength",
    "win_rate_l10",
    "form_composite",
})


class AccessTier(str, Enum):
    VISITOR = "visitor"
    FREE = "free"
    PREMIUM = "premium"


def resolve_access_tier(
    request: Request,
    user_row: dict[str, Any] | None = None,
) -> AccessTier:
    if is_premium_user(user_row, request):
        return AccessTier.PREMIUM
    session = get_user_session(request)
    if session:
        return AccessTier.FREE
    return AccessTier.VISITOR


def _redact_slate_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in _SLATE_PREMIUM_KEYS:
        out.pop(key, None)
    best = out.get("best_pick")
    if isinstance(best, dict):
        trimmed = dict(best)
        trimmed.pop("edge", None)
        trimmed.pop("ev", None)
        trimmed.pop("confidence", None)
        out["best_pick"] = trimmed
    return out


def _redact_single(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in (
        "edge",
        "ev",
        "line_strength",
        "win_rate_l10",
        "form_composite",
        "plus_ev",
        "ml_confidence",
        "confidence",
    ):
        out.pop(key, None)
    return out


def redact_home_summary(payload: dict[str, Any], tier: AccessTier) -> dict[str, Any]:
    if tier == AccessTier.PREMIUM:
        return {**payload, "access_tier": tier.value, "premium_required": False}

    out = copy.deepcopy(payload)
    out["access_tier"] = tier.value
    out["premium_required"] = True
    out.pop("plus_ev_singles", None)
    out.pop("plus_ev_totals", None)

    slate_index = out.get("slate_by_game_id") or {}
    if isinstance(slate_index, dict):
        out["slate_by_game_id"] = {
            gid: _redact_slate_row(row) for gid, row in slate_index.items()
        }

    singles = out.get("top_singles") or []
    if tier == AccessTier.VISITOR:
        out["top_singles"] = []
        out["free_pick_teaser"] = bool(singles)
    else:
        out["top_singles"] = [_redact_single(singles[0])] if singles else []

    return out


def redact_props_payload(payload: dict[str, Any], tier: AccessTier) -> dict[str, Any]:
    if tier == AccessTier.PREMIUM:
        return {**payload, "access_tier": tier.value, "premium_required": False}

    out = copy.deepcopy(payload)
    out["access_tier"] = tier.value
    out["premium_required"] = True
    props = out.get("top_props") or out.get("props") or []
    if tier == AccessTier.VISITOR:
        out["top_props"] = []
        out["props"] = []
    elif props:
        teaser = _redact_single(props[0] if isinstance(props[0], dict) else {})
        out["top_props"] = [teaser]
        out["props"] = [teaser]
    return out


def redact_pick_payload(payload: dict[str, Any], tier: AccessTier) -> dict[str, Any]:
    """Redact premium pick fields based on API response shape."""
    if "slate_by_game_id" in payload or "top_singles" in payload:
        return redact_home_summary(payload, tier)
    if "top_props" in payload or "props" in payload:
        return redact_props_payload(payload, tier)
    if "model" in payload or "highlights" in payload or "parlays" in payload:
        return redact_game_insights(payload, tier)
    if tier == AccessTier.PREMIUM:
        return {**payload, "access_tier": tier.value, "premium_required": False}
    out = copy.deepcopy(payload)
    out["access_tier"] = tier.value
    out["premium_required"] = True
    return out


def redact_game_insights(payload: dict[str, Any], tier: AccessTier) -> dict[str, Any]:
    if tier == AccessTier.PREMIUM:
        return {**payload, "access_tier": tier.value, "premium_required": False}

    out = copy.deepcopy(payload)
    out["access_tier"] = tier.value
    out["premium_required"] = True

    model = out.get("model")
    if isinstance(model, dict):
        for key in ("edge", "ev_confidence", "market_prob", "model_prob", "confidence"):
            model.pop(key, None)

    highlights = out.get("highlights")
    if isinstance(highlights, dict):
        for key in list(highlights.keys()):
            if key.endswith("_tier") or key.endswith("_edge"):
                highlights.pop(key, None)

    out.pop("parlays", None)
    out.pop("top_parlays", None)
    return out
