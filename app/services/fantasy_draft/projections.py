"""Season point projections — use explicit fields when present, else rank curves."""

from __future__ import annotations

from typing import Any

SCORING_RANK_KEY = {
    "standard": "rank_std",
    "half_ppr": "rank_half",
    "ppr": "rank_ppr",
}

SCORING_PROJ_KEY = {
    "standard": "proj_pts_std",
    "half_ppr": "proj_pts_half",
    "ppr": "proj_pts_ppr",
}

# Anchor curves: rank → approx full-season fantasy points by format/position.
# Used only when rankings JSON lacks proj_pts_* (documented fallback).
_POS_ANCHORS: dict[str, dict[str, list[tuple[int, float]]]] = {
    "half_ppr": {
        "QB": [(1, 380), (5, 340), (12, 300), (24, 260), (40, 200)],
        "RB": [(1, 320), (5, 280), (12, 240), (24, 190), (48, 130), (80, 90)],
        "WR": [(1, 310), (5, 275), (12, 235), (24, 185), (48, 125), (80, 85)],
        "TE": [(1, 250), (3, 210), (8, 170), (16, 130), (32, 95)],
        "DST": [(1, 140), (5, 120), (12, 100), (24, 80)],
        "K": [(1, 150), (5, 135), (12, 120), (24, 100)],
    },
    "ppr": {
        "QB": [(1, 380), (5, 340), (12, 300), (24, 260), (40, 200)],
        "RB": [(1, 350), (5, 310), (12, 265), (24, 210), (48, 145), (80, 100)],
        "WR": [(1, 360), (5, 320), (12, 275), (24, 215), (48, 150), (80, 105)],
        "TE": [(1, 280), (3, 235), (8, 190), (16, 145), (32, 105)],
        "DST": [(1, 140), (5, 120), (12, 100), (24, 80)],
        "K": [(1, 150), (5, 135), (12, 120), (24, 100)],
    },
    "standard": {
        "QB": [(1, 380), (5, 340), (12, 300), (24, 260), (40, 200)],
        "RB": [(1, 300), (5, 265), (12, 225), (24, 175), (48, 115), (80, 80)],
        "WR": [(1, 270), (5, 240), (12, 205), (24, 160), (48, 110), (80, 75)],
        "TE": [(1, 220), (3, 185), (8, 150), (16, 115), (32, 85)],
        "DST": [(1, 140), (5, 120), (12, 100), (24, 80)],
        "K": [(1, 150), (5, 135), (12, 120), (24, 100)],
    },
}


def rank_for_scoring(player: dict[str, Any], scoring: str) -> int:
    key = SCORING_RANK_KEY.get(scoring, SCORING_RANK_KEY["half_ppr"])
    raw = player.get(key)
    if raw is None:
        raw = player.get("adp") or player.get("rank_half") or 999
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 999


def _interp(rank: int, anchors: list[tuple[int, float]]) -> float:
    if rank <= anchors[0][0]:
        return anchors[0][1]
    for i in range(1, len(anchors)):
        r0, p0 = anchors[i - 1]
        r1, p1 = anchors[i]
        if rank <= r1:
            t = (rank - r0) / max(r1 - r0, 1)
            return p0 + (p1 - p0) * t
    r0, p0 = anchors[-1]
    # Soft decay beyond last anchor
    return max(40.0, p0 - (rank - r0) * 1.2)


def projected_fantasy_points(player: dict[str, Any], scoring: str) -> float:
    """League-scoring season projection; prefers explicit JSON fields."""
    proj_key = SCORING_PROJ_KEY.get(scoring, "proj_pts_half")
    raw = player.get(proj_key) or player.get("proj_pts")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    pos = str(player.get("position") or "WR")
    table = _POS_ANCHORS.get(scoring) or _POS_ANCHORS["half_ppr"]
    anchors = table.get(pos) or table.get("WR") or [(1, 200), (50, 100)]
    return round(_interp(rank_for_scoring(player, scoring), anchors), 2)


def power_rank_from_consensus(rank: int) -> int:
    return int(max(1, min(100, 101 - int(rank))))
