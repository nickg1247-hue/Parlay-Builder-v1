"""Compose normalized DraftScore / ERVA components."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.availability import adp_value, probability_available_next_pick
from app.services.fantasy_draft.projections import (
    power_rank_from_consensus,
    projected_fantasy_points,
    rank_for_scoring,
)
from app.services.fantasy_draft.roster import (
    lineup_impact,
    optimize_starting_lineup,
    projected_role_for_player,
    roster_imbalance_penalty,
    roster_need_score,
)
from app.services.fantasy_draft.scarcity import tier_for_player
from app.services.fantasy_draft.settings import LeagueSettings
from app.services.fantasy_draft.vorp import vorp_for_player


def _norm(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def score_candidate(
    player: dict[str, Any],
    *,
    settings: LeagueSettings,
    rostered: list[dict[str, Any]],
    open_needs: list[str],
    available: list[dict[str, Any]],
    replacement: dict[str, float],
    scarcity: dict[str, float],
    current_overall: int,
    team_next_overall: int,
    picks: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
    draft_progress: float,
    vorp_span: tuple[float, float],
    proj_span: tuple[float, float],
    impact_span: tuple[float, float],
) -> dict[str, Any]:
    w = settings.weights
    pos = str(player.get("position") or "")
    proj = projected_fantasy_points(player, settings.scoring)
    vorp = vorp_for_player(player, settings, replacement)
    tier_info = tier_for_player(player, available, settings)
    need = roster_need_score(
        player, open_needs, settings, draft_progress=draft_progress
    )
    impact = lineup_impact(rostered, player, settings)
    imbalance = roster_imbalance_penalty(rostered, player, settings, open_needs)
    scar = float(scarcity.get(pos, 0.0))
    tier_drop_n = _norm(float(tier_info["tier_drop"]), 0.0, 40.0)
    p_avail = probability_available_next_pick(
        player,
        settings=settings,
        current_overall=current_overall,
        team_next_overall=team_next_overall,
        picks=picks,
        players_by_id=players_by_id,
    )
    urgency = 1.0 - p_avail
    adp_v = adp_value(player, current_overall=current_overall, scoring=settings.scoring)
    # Upside proxy: early-tier + young ADP miss → mild; use tier + rank
    rank = rank_for_scoring(player, settings.scoring)
    upside = _norm(120 - min(rank, 120), 0, 120) * 0.5 + tier_drop_n * 0.5
    risk = 0.15 if player.get("injury_risk") else 0.05
    bye_pen = 0.0
    bye = player.get("bye")
    if bye is not None:
        if any(p.get("bye") == bye for p in rostered):
            bye_pen = 0.35

    components = {
        "projection": _norm(proj, proj_span[0], proj_span[1]),
        "vorp": _norm(vorp, vorp_span[0], vorp_span[1]),
        "scarcity": scar,
        "tier_drop": tier_drop_n,
        "roster_need": need,
        "lineup_impact": _norm(impact, impact_span[0], impact_span[1]),
        "adp_value": (adp_v + 1.0) / 2.0,
        "availability_urgency": urgency,
        "upside": upside,
        "risk": risk,
        "roster_imbalance": imbalance,
        "bye": bye_pen,
    }

    # Signed contributions
    score = 0.0
    score += w["projection"] * components["projection"]
    score += w["vorp"] * components["vorp"]
    score += w["scarcity"] * components["scarcity"]
    score += w["tier_drop"] * components["tier_drop"]
    score += w["roster_need"] * components["roster_need"]
    score += w["lineup_impact"] * components["lineup_impact"]
    score += w["adp_value"] * components["adp_value"]
    score += w["availability_urgency"] * components["availability_urgency"]
    score += w["upside"] * components["upside"]
    score -= w["risk"] * components["risk"]
    score -= w["roster_imbalance"] * components["roster_imbalance"]
    score -= w["bye"] * components["bye"]

    # Bench value rises with draft progress (opportunity-cost aware via other terms)
    role = projected_role_for_player(rostered, player, settings)
    if role.get("is_bench"):
        bench_value = (
            components["upside"] * 0.35
            + components["vorp"] * 0.25
            + components["adp_value"] * 0.15
            + components["tier_drop"] * 0.15
            + (1.0 - imbalance) * 0.1
        ) * (0.35 + 0.65 * draft_progress)
        score += w.get("upside", 0.05) * 0.5 * bench_value * draft_progress
        components["bench_value"] = round(bench_value, 4)

    # Map to 0–100 display score
    display = round(max(0.0, min(99.5, 40.0 + score * 55.0)), 2)

    lineup_after = optimize_starting_lineup(rostered + [player], settings)

    return {
        "player_id": player["player_id"],
        "name": player.get("name"),
        "position": pos,
        "team": player.get("team"),
        "bye": player.get("bye"),
        "rank": rank,
        "adp": player.get("adp"),
        "projected_pick": round(float(player.get("adp") or rank), 1),
        "power_rank": power_rank_from_consensus(rank),
        "projected_points": round(proj, 1),
        "vorp": round(vorp, 2),
        "tier": tier_info["label"],
        "tier_drop": tier_info["tier_drop"],
        "fit_pct": int(round(display)),
        "score": display,
        "erva": round(score, 4),
        "p_available_next": round(p_avail, 3),
        "lineup_impact": round(impact, 2),
        "projected_role": role,
        "roster_impact": {
            "lineup_delta": round(impact, 2),
            "projected_starter_points_after": lineup_after["projected_starter_points"],
            "counts_after": lineup_after["counts"],
        },
        "components": {k: round(v, 4) for k, v in components.items()},
        "reasons": [],  # filled by engine
    }
