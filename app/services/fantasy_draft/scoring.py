"""Compose normalized DraftScore / ERVA components — team usefulness over raw proj."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.availability import adp_value, probability_available_next_pick
from app.services.fantasy_draft.projections import (
    power_rank_from_consensus,
    projected_fantasy_points,
    rank_for_scoring,
)
from app.services.fantasy_draft.roster import (
    flex_lineup_impact,
    lineup_impact,
    optimize_starting_lineup,
    projected_role_for_player,
    roster_imbalance_penalty,
    roster_need_score,
    starter_need_urgency,
)
from app.services.fantasy_draft.scarcity import tier_for_player
from app.services.fantasy_draft.settings import LeagueSettings, WRT_ELIGIBLE
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
    flex_span: tuple[float, float] | None = None,
    remaining_team_picks: int | None = None,
) -> dict[str, Any]:
    w = dict(settings.weights)
    # Two WRT slots: lean harder into construction vs raw projection
    if settings.wrt_slots >= 2:
        w["lineup_impact"] = w.get("lineup_impact", 0.16) + 0.03
        w["roster_need"] = w.get("roster_need", 0.18) + 0.02
        w["projection"] = max(0.04, w.get("projection", 0.06) - 0.02)

    rem = (
        remaining_team_picks
        if remaining_team_picks is not None
        else max(1, settings.rounds - len(rostered))
    )
    pos = str(player.get("position") or "")
    proj = projected_fantasy_points(player, settings.scoring)
    vorp = vorp_for_player(player, settings, replacement)
    tier_info = tier_for_player(player, available, settings)
    need = roster_need_score(
        player,
        open_needs,
        settings,
        draft_progress=draft_progress,
        rostered=rostered,
        available=available,
        remaining_team_picks=rem,
    )
    need_urgency = starter_need_urgency(
        open_needs,
        settings=settings,
        draft_progress=draft_progress,
        remaining_team_picks=rem,
        available=available,
        position=pos,
    )
    impact = lineup_impact(rostered, player, settings)
    flex = flex_lineup_impact(rostered, player, settings)
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
    rank = rank_for_scoring(player, settings.scoring)
    upside = _norm(120 - min(rank, 120), 0, 120) * 0.5 + tier_drop_n * 0.5
    risk = 0.15 if player.get("injury_risk") else 0.05
    bye_pen = 0.0
    bye = player.get("bye")
    if bye is not None:
        if any(p.get("bye") == bye for p in rostered):
            bye_pen = 0.35

    flex_delta = float(flex.get("delta") or 0.0)
    f_span = flex_span or (0.0, max(40.0, flex_delta))
    flex_n = _norm(flex_delta, f_span[0], f_span[1])

    combined_impact = _norm(impact, impact_span[0], impact_span[1])
    if pos in WRT_ELIGIBLE and settings.wrt_slots > 0:
        combined_impact = min(1.0, 0.65 * combined_impact + 0.35 * flex_n)

    proj_n = _norm(proj, proj_span[0], proj_span[1])
    # Soft-cap projection when team still has holes this player does not fill
    holes = [s for s in open_needs if str(s).upper() not in ("BENCH", "IR")]
    fills_hole = need >= 0.45
    if holes and not fills_hole:
        proj_n *= 0.55 + 0.25 * (1.0 - need_urgency)
    elif holes and fills_hole:
        # Need fill gets a mild construction boost over pure proj ranking
        proj_n = min(1.0, proj_n * 0.85 + 0.15)

    # Team usefulness: how much this player helps *this* roster right now
    team_usefulness = min(
        1.0,
        0.40 * need
        + 0.30 * combined_impact
        + 0.15 * (1.0 - imbalance)
        + 0.10 * scar
        + 0.05 * flex_n,
    )
    # Scale usefulness by how urgent holes are overall
    if holes:
        team_usefulness = min(1.0, team_usefulness * (0.7 + 0.3 * need_urgency))

    components = {
        "projection": proj_n,
        "vorp": _norm(vorp, vorp_span[0], vorp_span[1]),
        "scarcity": scar,
        "tier_drop": tier_drop_n,
        "roster_need": need,
        "starter_need_urgency": need_urgency,
        "lineup_impact": combined_impact,
        "flex_lineup_impact": flex_n,
        "team_usefulness": team_usefulness,
        "adp_value": (adp_v + 1.0) / 2.0,
        "availability_urgency": urgency,
        "upside": upside,
        "risk": risk,
        "roster_imbalance": imbalance,
        "bye": bye_pen,
    }

    score = 0.0
    score += w["projection"] * components["projection"]
    score += w["vorp"] * components["vorp"]
    score += w["scarcity"] * components["scarcity"]
    score += w["tier_drop"] * components["tier_drop"]
    score += w["roster_need"] * components["roster_need"]
    score += w["lineup_impact"] * components["lineup_impact"]
    score += w.get("team_usefulness", 0.10) * components["team_usefulness"]
    score += w["adp_value"] * components["adp_value"]
    score += w["availability_urgency"] * components["availability_urgency"]
    score += w["upside"] * components["upside"]
    score -= w["risk"] * components["risk"]
    score -= w["roster_imbalance"] * components["roster_imbalance"]
    score -= w["bye"] * components["bye"]

    role = projected_role_for_player(rostered, player, settings)
    if role.get("is_bench"):
        # Bench only competes when usefulness is still competitive or holes are soft
        bench_value = (
            components["upside"] * 0.30
            + components["vorp"] * 0.25
            + components["adp_value"] * 0.15
            + components["tier_drop"] * 0.15
            + (1.0 - imbalance) * 0.15
        ) * (0.30 + 0.70 * draft_progress)
        if holes and need_urgency >= 0.7:
            bench_value *= 0.55  # hard to justify bench while urgent holes remain
        score += w.get("upside", 0.05) * 0.5 * bench_value
        components["bench_value"] = round(bench_value, 4)

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
        "flex_lineup_impact": flex,
        "team_usefulness": round(team_usefulness, 3),
        "starter_need_urgency": round(need_urgency, 3),
        "projected_role": role,
        "roster_impact": {
            "lineup_delta": round(impact, 2),
            "flex_delta": flex.get("delta"),
            "wrt_floor": flex.get("wrt_floor"),
            "open_needs": list(open_needs),
            "projected_starter_points_after": lineup_after["projected_starter_points"],
            "counts_after": lineup_after["counts"],
        },
        "components": {k: round(v, 4) for k, v in components.items()},
        "reasons": [],
    }
