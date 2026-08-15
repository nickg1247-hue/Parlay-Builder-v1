"""Dynamic positional scarcity and tier drops from the live pool."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.projections import projected_fantasy_points
from app.services.fantasy_draft.settings import POSITION_KEYS, LeagueSettings, WRT_ELIGIBLE
from app.services.fantasy_draft.vorp import allocate_wrt_demand


def _sorted_pts(
    available: list[dict[str, Any]], settings: LeagueSettings, position: str
) -> list[float]:
    pts = [
        projected_fantasy_points(p, settings.scoring)
        for p in available
        if str(p.get("position") or "") == position
    ]
    pts.sort(reverse=True)
    return pts


def positional_scarcity(
    available: list[dict[str, Any]],
    settings: LeagueSettings,
) -> dict[str, float]:
    """
    0–1 scarcity: larger cliff between best remaining and depth = higher.

    For RB/WR/TE, WRT demand tightens the effective starter pool — two WRT
    slots make skill positions scarcer than named starters alone imply.
    """
    demand = allocate_wrt_demand(available, settings)
    out: dict[str, float] = {}
    for pos in POSITION_KEYS:
        pts = _sorted_pts(available, settings, pos)
        if len(pts) < 2:
            out[pos] = 0.55 if pts else 0.0
            continue
        best = pts[0]
        # Compare deeper into the board when WRT inflates starter demand
        demand_n = max(1.0, float(demand.get(pos, 1.0)))
        idx = min(
            len(pts) - 1,
            max(2, int(round(settings.league_size * min(demand_n, 3.5) * 0.45))),
        )
        cliff = best - pts[idx]
        scale = 35.0 if pos in ("RB", "WR", "TE", "QB") else 20.0
        base = max(0.0, min(1.0, cliff / scale))

        # WRT demand premium: positions that fill more flex slots get a bump
        if pos in WRT_ELIGIBLE and settings.wrt_slots > 0:
            dedicated = sum(1 for s in settings.starter_slots if s == pos)
            flex_share = max(0.0, demand_n - dedicated)
            # Up to +0.18 when this position is heavily used in WRT
            base = min(1.0, base + 0.06 * settings.wrt_slots + 0.08 * min(1.5, flex_share))

        out[pos] = base
    return out


def tier_for_player(
    player: dict[str, Any],
    available: list[dict[str, Any]],
    settings: LeagueSettings,
    *,
    gap: float = 12.0,
) -> dict[str, Any]:
    """Dynamic tier index within position among available players."""
    pos = str(player.get("position") or "")
    pts = projected_fantasy_points(player, settings.scoring)
    same = sorted(
        (
            projected_fantasy_points(p, settings.scoring)
            for p in available
            if str(p.get("position") or "") == pos
        ),
        reverse=True,
    )
    if not same:
        return {"tier": int(player.get("tier") or 99), "tier_drop": 0.0, "label": "—"}

    tiers: list[list[float]] = [[same[0]]]
    for x in same[1:]:
        if tiers[-1][0] - x >= gap:
            tiers.append([x])
        else:
            tiers[-1].append(x)

    tier_idx = 1
    for i, group in enumerate(tiers, start=1):
        if pts >= min(group) - 0.01:
            tier_idx = i
            break
    else:
        tier_idx = len(tiers)

    # Drop to next tier floor
    drop = 0.0
    if tier_idx < len(tiers):
        drop = max(0.0, pts - max(tiers[tier_idx]))
    elif len(same) > 1:
        try:
            i = same.index(pts)
            if i + 1 < len(same):
                drop = max(0.0, pts - same[i + 1])
        except ValueError:
            drop = 0.0

    static = player.get("tier")
    label = f"{pos} Tier {tier_idx}"
    return {
        "tier": int(static) if static is not None else tier_idx,
        "dynamic_tier": tier_idx,
        "tier_drop": round(drop, 2),
        "label": label,
    }


def recent_position_run(
    picks: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
    *,
    window: int = 8,
) -> dict[str, Any]:
    if not picks:
        return {"window": window, "counts": {}, "hot": None}
    recent = sorted(picks, key=lambda p: int(p["overall"]))[-window:]
    counts: dict[str, int] = {}
    for p in recent:
        pl = players_by_id.get(str(p["player_id"]))
        if not pl:
            continue
        pos = str(pl.get("position") or "")
        counts[pos] = counts.get(pos, 0) + 1
    hot = max(counts, key=counts.get) if counts else None
    return {"window": window, "counts": counts, "hot": hot}
