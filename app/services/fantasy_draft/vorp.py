"""Replacement levels and VORP — WRT demand included in starter demand."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.projections import projected_fantasy_points
from app.services.fantasy_draft.settings import POSITION_KEYS, LeagueSettings, WRT_ELIGIBLE


def starter_demand(settings: LeagueSettings) -> dict[str, float]:
    """
    How many starter-quality slots the league consumes per position.

    Dedicated slots count 1:1. WRT/FLEX slots are allocated dynamically across
    RB/WR/TE from the available pool's relative strength at the flex margin
    when `available` is passed to `replacement_levels`; here we use a neutral
    prior that is refined by `allocate_wrt_demand`.
    """
    demand = {k: 0.0 for k in POSITION_KEYS}
    wrt = 0
    sflex = 0
    for slot in settings.starter_slots:
        s = str(slot).upper()
        if s in demand:
            demand[s] += 1
        elif s in ("WRT", "FLEX"):
            wrt += 1
        elif s in ("SUPERFLEX", "SF"):
            sflex += 1
    # Prior split — refined when pool is known
    if wrt:
        demand["RB"] += wrt * 0.38
        demand["WR"] += wrt * 0.47
        demand["TE"] += wrt * 0.15
    if sflex:
        demand["QB"] += sflex * 0.7
        demand["RB"] += sflex * 0.1
        demand["WR"] += sflex * 0.15
        demand["TE"] += sflex * 0.05
    return demand


def allocate_wrt_demand(
    available: list[dict[str, Any]],
    settings: LeagueSettings,
) -> dict[str, float]:
    """
    Estimate how WRT starters are filled from RB/WR/TE based on player values
    at the flex margin (dynamic, not fixed 40/45/15 forever).
    """
    demand = {k: 0.0 for k in POSITION_KEYS}
    for slot in settings.starter_slots:
        s = str(slot).upper()
        if s in demand:
            demand[s] += 1.0

    wrt_n = sum(
        1 for s in settings.starter_slots if str(s).upper() in ("WRT", "FLEX")
    )
    sflex_n = sum(
        1 for s in settings.starter_slots if str(s).upper() in ("SUPERFLEX", "SF")
    )

    if wrt_n:
        # Players beyond dedicated starter demand form the flex pool
        dedicated = {
            "RB": demand["RB"],
            "WR": demand["WR"],
            "TE": demand["TE"],
        }
        by_pos: dict[str, list[float]] = {k: [] for k in WRT_ELIGIBLE}
        for p in available:
            pos = str(p.get("position") or "")
            if pos in by_pos:
                by_pos[pos].append(projected_fantasy_points(p, settings.scoring))
        for pos in by_pos:
            by_pos[pos].sort(reverse=True)

        flex_cands: list[tuple[str, float]] = []
        for pos in WRT_ELIGIBLE:
            start = int(round(settings.league_size * dedicated[pos]))
            series = by_pos[pos]
            for pts in series[start : start + max(8, wrt_n * settings.league_size)]:
                flex_cands.append((pos, pts))
        flex_cands.sort(key=lambda t: -t[1])
        take = max(1, wrt_n * settings.league_size)
        slice_ = flex_cands[:take]
        if slice_:
            totals = {p: 0 for p in WRT_ELIGIBLE}
            for pos, _ in slice_:
                totals[pos] += 1
            total = sum(totals.values()) or 1
            for pos in WRT_ELIGIBLE:
                demand[pos] += wrt_n * (totals[pos] / total)
        else:
            demand["RB"] += wrt_n * 0.38
            demand["WR"] += wrt_n * 0.47
            demand["TE"] += wrt_n * 0.15

    if sflex_n:
        demand["QB"] += sflex_n * 0.7
        demand["RB"] += sflex_n * 0.1
        demand["WR"] += sflex_n * 0.15
        demand["TE"] += sflex_n * 0.05

    return demand


def replacement_levels(
    available: list[dict[str, Any]],
    settings: LeagueSettings,
) -> dict[str, float]:
    """
    Replacement = projected points of the Nth remaining player at position,
    where N ≈ league_size * starter_demand (with WRT allocation) + bench buffer.
    """
    demand = allocate_wrt_demand(available, settings)
    by_pos: dict[str, list[float]] = {k: [] for k in POSITION_KEYS}
    for p in available:
        pos = str(p.get("position") or "")
        if pos in by_pos:
            by_pos[pos].append(projected_fantasy_points(p, settings.scoring))
    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    levels: dict[str, float] = {}
    for pos in POSITION_KEYS:
        buffer = 0.5 if pos in ("RB", "WR", "TE", "QB") else 0.15
        n = max(1, int(round(settings.league_size * (demand.get(pos, 1) + buffer))))
        series = by_pos[pos]
        if not series:
            levels[pos] = 0.0
        elif n - 1 < len(series):
            levels[pos] = series[n - 1]
        else:
            levels[pos] = series[-1] * 0.85
    return levels


def vorp_for_player(
    player: dict[str, Any],
    settings: LeagueSettings,
    levels: dict[str, float],
) -> float:
    pos = str(player.get("position") or "")
    pts = projected_fantasy_points(player, settings.scoring)
    return pts - float(levels.get(pos, 0.0))
