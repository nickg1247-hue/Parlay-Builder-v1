"""DraftEngine — recommend, CPU select, apply_pick, mock stress sims."""

from __future__ import annotations

import random
from typing import Any

from app.services.fantasy_draft.availability import (
    next_overall_for_team,
    team_slot_for_overall,
)
from app.services.fantasy_draft.eligibility import (
    available_pool,
    can_team_draft_player,
    count_by_position,
    get_eligible_players,
    team_roster_from_picks,
    validate_pick,
)
from app.services.fantasy_draft.projections import projected_fantasy_points
from app.services.fantasy_draft.roster import (
    compute_open_needs,
    optimize_starting_lineup,
)
from app.services.fantasy_draft.scarcity import positional_scarcity, recent_position_run
from app.services.fantasy_draft.scoring import score_candidate
from app.services.fantasy_draft.settings import LeagueSettings, league_settings_from_request
from app.services.fantasy_draft.vorp import replacement_levels


CPU_PERSONALITIES: dict[str, dict[str, float]] = {
    "balanced": {},
    "rb_heavy": {"roster_need": 0.02, "vorp": 0.03},
    "wr_heavy": {"roster_need": 0.02},
    "late_qb": {"availability_urgency": -0.03},
    "early_qb": {"roster_need": 0.04, "scarcity": 0.03},
    "upside": {"upside": 0.06, "risk": -0.02},
}


def _next_overall(picks: list[dict[str, Any]]) -> int:
    if not picks:
        return 1
    return max(int(p["overall"]) for p in picks) + 1


def _build_explanations(row: dict[str, Any], *, open_needs: list[str]) -> list[str]:
    reasons: list[str] = []
    c = row.get("components") or {}
    role = row.get("projected_role") or {}
    impact = float(row.get("lineup_impact") or 0)

    if role.get("is_bench") and open_needs:
        open_label = ", ".join(str(s) for s in open_needs[:3])
        p_av = row.get("p_available_next")
        if p_av is not None:
            reasons.append(
                f"BENCH PICK — VALUE OVERRIDE: {open_label} still open, but this "
                f"player's board value outweighs waiting "
                f"(≈{int(p_av * 100)}% context on skill survival to next pick)."
            )
        else:
            reasons.append(
                f"BENCH PICK — VALUE OVERRIDE: {open_label} remains open; "
                "waiting is estimated better than taking a weaker fill now."
            )
    elif role.get("is_wrt") and impact >= 8:
        reasons.append(
            f"Adds {impact:.0f} projected points to your optimal lineup "
            f"as {role.get('label', 'WRT')}."
        )
    elif role.get("is_starter") and impact >= 8:
        reasons.append(
            f"Adds {impact:.0f} projected starter points "
            f"({role.get('label', row.get('position'))})."
        )
    elif impact >= 20:
        reasons.append(f"Lineup +{impact:.0f} pts")

    if row.get("vorp", 0) >= 25:
        reasons.append(f"Strong VORP (+{row['vorp']:.0f})")
    if c.get("scarcity", 0) >= 0.55:
        reasons.append(f"Scarce at {row.get('position')}")
    if row.get("tier_drop", 0) >= 15:
        reasons.append(f"Major tier drop ({row.get('tier')})")
    if c.get("roster_need", 0) >= 0.55 and not role.get("is_bench"):
        slot = row.get("position")
        norm = [str(s).upper() for s in open_needs]
        if slot in norm:
            reasons.append(f"Fills {slot} starter need")
        elif any(s in ("WRT", "FLEX") for s in norm):
            reasons.append("Strong WRT / starter fit")
        else:
            reasons.append("Matches roster need")
    p_av = row.get("p_available_next")
    if p_av is not None and p_av <= 0.25 and not role.get("is_bench"):
        reasons.append(f"Only {int(p_av * 100)}% chance survives to your next pick")
    elif p_av is not None and p_av >= 0.7 and not role.get("is_bench"):
        reasons.append("Likely available later — value elsewhere may be higher")
    if c.get("adp_value", 0.5) >= 0.65:
        reasons.append("Falling vs ADP (value)")
    if c.get("roster_imbalance", 0) >= 0.45:
        reasons.append("Depth risk at this position")
    if role.get("is_bench") and not open_needs:
        reasons.append(
            "Starting lineup is filled — highest remaining upside for bench depth"
        )
    if not reasons:
        reasons.append("Best expected roster value among legal options")
    return reasons[:5]


def _why_not(primary: dict[str, Any], alt: dict[str, Any]) -> str:
    if alt.get("position") != primary.get("position"):
        if (primary.get("p_available_next") or 1) < (alt.get("p_available_next") or 1):
            return (
                f"Strong option, but {primary['name']} is less likely to reach your next pick "
                f"while similar {alt['position']} depth may remain."
            )
        if (primary.get("lineup_impact") or 0) > (alt.get("lineup_impact") or 0) + 8:
            return (
                f"{primary['name']} improves projected starters more "
                f"(+{primary.get('lineup_impact', 0):.0f} vs +{alt.get('lineup_impact', 0):.0f})."
            )
    if (primary.get("vorp") or 0) > (alt.get("vorp") or 0) + 10:
        return f"Lower VORP than {primary['name']} in the current pool."
    if (alt.get("components") or {}).get("roster_imbalance", 0) >= 0.4:
        return "Adds excess depth while other starter holes remain."
    return f"Slightly lower expected roster value than {primary['name']}."


def _lookahead_bonus(
    player: dict[str, Any],
    *,
    settings: LeagueSettings,
    picks: list[dict[str, Any]],
    players: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
    team_slot: int,
    current_overall: int,
    scored_index: dict[str, dict[str, Any]],
) -> float:
    """Lightweight: assume we take player, opponents take BPA-for-them until our next pick."""
    team_next = next_overall_for_team(
        settings.league_size, team_slot, current_overall + 1, settings.total_picks
    )
    if team_next is None:
        return 0.0

    sim_picks = list(picks) + [
        {
            "overall": current_overall,
            "team_slot": team_slot,
            "player_id": player["player_id"],
        }
    ]
    # Opponents: pick highest erva among THEIR eligible from a cheap proxy (global score map)
    for o in range(current_overall + 1, team_next):
        slot = team_slot_for_overall(o, settings.league_size)
        pool = available_pool(players, sim_picks)
        roster = team_roster_from_picks(sim_picks, players_by_id, slot)
        eligible = get_eligible_players(roster, pool, settings)
        if not eligible:
            continue
        # Prefer players that score well globally and fill their needs
        needs = compute_open_needs(roster, settings)

        def key(p: dict[str, Any]) -> float:
            row = scored_index.get(str(p["player_id"]))
            base = float((row or {}).get("erva") or 0)
            pos = str(p.get("position") or "")
            if pos in needs:
                base += 0.08
            return base

        choice = max(eligible, key=key)
        sim_picks.append(
            {"overall": o, "team_slot": slot, "player_id": choice["player_id"]}
        )

    # Best eligible at our next pick
    pool = available_pool(players, sim_picks)
    roster = team_roster_from_picks(sim_picks, players_by_id, team_slot)
    eligible = get_eligible_players(roster, pool, settings)
    if not eligible:
        return 0.0
    best = max(
        (scored_index.get(str(p["player_id"]), {}).get("erva") or 0) for p in eligible
    )
    # Combined path value vs taking this player alone
    now = float(scored_index.get(str(player["player_id"]), {}).get("erva") or 0)
    return now * 0.55 + float(best) * 0.45


def recommend_for_team(
    players: list[dict[str, Any]],
    *,
    settings: LeagueSettings,
    team_slot: int,
    picks: list[dict[str, Any]],
    alternate_count: int = 5,
    debug: bool = False,
    run_lookahead: bool = True,
) -> dict[str, Any]:
    """
    Team-specific recommendations. HARD-filters through get_eligible_players first.
    """
    if team_slot < 1 or team_slot > settings.league_size:
        raise ValueError("team_slot out of range")

    players_by_id = {str(p["player_id"]): p for p in players}
    current = _next_overall(picks)
    on_clock = (
        team_slot_for_overall(current, settings.league_size)
        if current <= settings.total_picks
        else None
    )
    team_next = next_overall_for_team(
        settings.league_size, team_slot, current, settings.total_picks
    )
    rostered = team_roster_from_picks(picks, players_by_id, team_slot)
    open_needs = compute_open_needs(rostered, settings)
    lineup = optimize_starting_lineup(rostered, settings)
    pool = available_pool(players, picks)
    eligible = get_eligible_players(rostered, pool, settings)

    meta = {
        "current_overall": current if current <= settings.total_picks else None,
        "on_clock_slot": on_clock,
        "user_on_clock": on_clock == team_slot and current <= settings.total_picks,
        "user_next_overall": team_next,
        "user_needs": open_needs,
        "scoring": settings.scoring,
        "league_size": settings.league_size,
        "total_picks": settings.total_picks,
        "picks_made": len(picks),
        "roster_size": settings.rounds,
        "roster_capacity": settings.roster_capacity,
        "slot_counts": dict(settings.slot_counts),
        "bench_slots": settings.bench_slots,
        "wrt_slots": settings.wrt_slots,
        "position_maxes": dict(settings.position_maxes),
        "user_position_counts": count_by_position(rostered),
        "clock_position_counts": count_by_position(
            team_roster_from_picks(picks, players_by_id, on_clock)
        )
        if on_clock
        else {},
        "starter_template": list(settings.starter_slots),
        "full_template": list(settings.full_template),
        "lineup": {
            "projected_starter_points": lineup["projected_starter_points"],
            "counts": lineup["counts"],
            "open_starter_slots": lineup["open_starter_slots"],
            "starters": [
                {
                    "slot": r["slot"],
                    "player_id": (r["player"] or {}).get("player_id")
                    if r.get("player")
                    else None,
                    "name": (r["player"] or {}).get("name") if r.get("player") else None,
                    "position": (r["player"] or {}).get("position")
                    if r.get("player")
                    else None,
                    "projected_points": r["projected_points"],
                    "role": r["role"],
                }
                for r in lineup["starters"]
            ],
            "bench": [
                {
                    "player_id": p.get("player_id"),
                    "name": p.get("name"),
                    "position": p.get("position"),
                }
                for p in lineup["bench"]
            ],
        },
        "superflex": settings.superflex,
        "position_outlook": {},
        "recent_run": recent_position_run(picks, players_by_id),
    }

    if team_next is None or not eligible:
        return {
            "primary": None,
            "alternates": [],
            "top_pool": [],
            "board_meta": meta,
            "error": None
            if team_next is None
            else {
                "type": "no_eligible_players",
                "team_slot": team_slot,
                "counts": count_by_position(rostered),
                "maxes": dict(settings.position_maxes),
                "open_needs": open_needs,
            },
        }

    replacement = replacement_levels(pool, settings)
    scarcity = positional_scarcity(pool, settings)
    meta["position_outlook"] = {
        pos: ("Scarce" if v >= 0.65 else "Moderate" if v >= 0.35 else "Deep")
        for pos, v in scarcity.items()
    }

    draft_progress = (team_next - 1) / max(settings.total_picks - 1, 1)
    # Spans for normalization from eligible set
    projs = [projected_fantasy_points(p, settings.scoring) for p in eligible]
    vorps = [
        projected_fantasy_points(p, settings.scoring) - replacement.get(str(p.get("position") or ""), 0)
        for p in eligible
    ]
    impacts = [0.0]  # filled below per candidate; use provisional span
    for p in eligible[:40]:
        from app.services.fantasy_draft.roster import lineup_impact

        impacts.append(lineup_impact(rostered, p, settings))

    proj_span = (min(projs), max(projs)) if projs else (0.0, 1.0)
    vorp_span = (min(vorps), max(vorps)) if vorps else (0.0, 1.0)
    impact_span = (min(impacts), max(impacts)) if impacts else (0.0, 1.0)
    if proj_span[0] == proj_span[1]:
        proj_span = (proj_span[0] - 1, proj_span[1] + 1)
    if vorp_span[0] == vorp_span[1]:
        vorp_span = (vorp_span[0] - 1, vorp_span[1] + 1)
    if impact_span[0] == impact_span[1]:
        impact_span = (impact_span[0] - 1, impact_span[1] + 1)

    scored: list[dict[str, Any]] = []
    for p in eligible:
        row = score_candidate(
            p,
            settings=settings,
            rostered=rostered,
            open_needs=open_needs,
            available=pool,
            replacement=replacement,
            scarcity=scarcity,
            current_overall=current,
            team_next_overall=team_next,
            picks=picks,
            players_by_id=players_by_id,
            draft_progress=draft_progress,
            vorp_span=vorp_span,
            proj_span=proj_span,
            impact_span=impact_span,
        )
        scored.append(row)

    scored.sort(key=lambda r: (-r["erva"], r["rank"], r["name"] or ""))
    scored_index = {str(r["player_id"]): r for r in scored}

    if run_lookahead and scored:
        top_n = scored[: min(12, len(scored))]
        for row in top_n:
            bonus = _lookahead_bonus(
                players_by_id[str(row["player_id"])],
                settings=settings,
                picks=picks,
                players=players,
                players_by_id=players_by_id,
                team_slot=team_slot,
                current_overall=current,
                scored_index=scored_index,
            )
            w_look = settings.weights.get("lookahead", 0.08)
            row["erva"] = round(row["erva"] + w_look * bonus, 4)
            row["score"] = round(max(0.0, min(99.5, 40.0 + row["erva"] * 55.0)), 2)
            row["fit_pct"] = int(round(row["score"]))
            if debug:
                row["components"]["lookahead"] = round(bonus, 4)
        scored.sort(key=lambda r: (-r["erva"], r["rank"], r["name"] or ""))

    for row in scored:
        row["reasons"] = _build_explanations(row, open_needs=open_needs)
        if not debug:
            row.pop("components", None)

    primary = scored[0] if scored else None
    alts = scored[1 : 1 + max(0, alternate_count)]
    if primary:
        for alt in alts:
            alt["why_not"] = _why_not(primary, alt)

    return {
        "primary": primary,
        "alternates": alts,
        "top_pool": scored[:40],
        "board_meta": meta,
        "error": None,
    }


def apply_pick(
    *,
    player_id: str,
    picks: list[dict[str, Any]],
    players: list[dict[str, Any]],
    settings: LeagueSettings,
    overall: int | None = None,
    team_slot: int | None = None,
) -> dict[str, Any]:
    """Validate + append pick. Returns {ok, picks, error?}."""
    players_by_id = {str(p["player_id"]): p for p in players}
    o = int(overall) if overall is not None else _next_overall(picks)
    slot = (
        int(team_slot)
        if team_slot is not None
        else team_slot_for_overall(o, settings.league_size)
    )
    check = validate_pick(
        player_id=player_id,
        team_slot=slot,
        overall=o,
        picks=picks,
        players_by_id=players_by_id,
        settings=settings,
    )
    if not check.get("ok"):
        return {"ok": False, "picks": picks, "error": check}
    new_picks = list(picks) + [
        {"overall": o, "team_slot": slot, "player_id": str(player_id)}
    ]
    return {"ok": True, "picks": new_picks, "error": None}


def cpu_select_player(
    players: list[dict[str, Any]],
    *,
    settings: LeagueSettings,
    team_slot: int,
    picks: list[dict[str, Any]],
    personality: str = "balanced",
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """CPU uses same engine; soft variance among top-3 legal recommendations."""
    rng = rng or random.Random()
    deltas = CPU_PERSONALITIES.get(personality, {})
    from app.services.fantasy_draft.settings import with_weights

    tuned = with_weights(settings, **deltas) if deltas else settings
    rec = recommend_for_team(
        players,
        settings=tuned,
        team_slot=team_slot,
        picks=picks,
        alternate_count=3,
        run_lookahead=False,
    )
    if rec.get("error") and not rec.get("primary"):
        return {"ok": False, "error": rec["error"]}
    cands = []
    if rec.get("primary"):
        cands.append(rec["primary"])
    for alt in rec.get("alternates") or []:
        if alt and alt.get("player_id") not in {c.get("player_id") for c in cands}:
            cands.append(alt)
    cands = cands[:3]
    if not cands:
        # Absolute fallback: first eligible by VORP proxy
        players_by_id = {str(p["player_id"]): p for p in players}
        roster = team_roster_from_picks(picks, players_by_id, team_slot)
        eligible = get_eligible_players(roster, available_pool(players, picks), settings)
        if not eligible:
            return {
                "ok": False,
                "error": {
                    "type": "no_eligible_players",
                    "team_slot": team_slot,
                    "counts": count_by_position(roster),
                    "maxes": dict(settings.position_maxes),
                },
            }
        eligible.sort(
            key=lambda p: -projected_fantasy_points(p, settings.scoring)
        )
        choice = eligible[0]
        applied = apply_pick(
            player_id=str(choice["player_id"]),
            picks=picks,
            players=players,
            settings=settings,
        )
        return {**applied, "choice": choice, "personality": personality}

    # Weighted: 70% #1, 20% #2, 10% #3
    weights = [0.7, 0.2, 0.1][: len(cands)]
    # renormalize
    s = sum(weights)
    weights = [w / s for w in weights]
    pick_row = rng.choices(cands, weights=weights, k=1)[0]
    # Final hard validation
    applied = apply_pick(
        player_id=str(pick_row["player_id"]),
        picks=picks,
        players=players,
        settings=settings,
    )
    return {**applied, "choice": pick_row, "personality": personality}


def simulate_full_draft(
    players: list[dict[str, Any]],
    *,
    settings: LeagueSettings,
    personalities: dict[int, str] | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Stress test: complete snake draft; report max/dupe violations (must be 0)."""
    rng = random.Random(seed)
    picks: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    personalities = personalities or {}
    for o in range(1, settings.total_picks + 1):
        slot = team_slot_for_overall(o, settings.league_size)
        pers = personalities.get(slot, rng.choice(list(CPU_PERSONALITIES)))
        result = cpu_select_player(
            players,
            settings=settings,
            team_slot=slot,
            picks=picks,
            personality=pers,
            rng=rng,
        )
        if not result.get("ok"):
            violations.append(
                {"overall": o, "team_slot": slot, "error": result.get("error")}
            )
            break
        picks = result["picks"]

    # Verify final rosters
    players_by_id = {str(p["player_id"]): p for p in players}
    for slot in range(1, settings.league_size + 1):
        roster = team_roster_from_picks(picks, players_by_id, slot)
        counts = count_by_position(roster)
        for pos, n in counts.items():
            mx = settings.position_maxes.get(pos, 99)
            if n > mx:
                violations.append(
                    {
                        "type": "position_max",
                        "team_slot": slot,
                        "position": pos,
                        "count": n,
                        "max": mx,
                    }
                )
    ids = [str(p["player_id"]) for p in picks]
    if len(ids) != len(set(ids)):
        violations.append({"type": "duplicate_players"})

    return {
        "picks": picks,
        "violations": violations,
        "ok": len(violations) == 0,
        "picks_made": len(picks),
        "expected_picks": settings.total_picks,
    }


def recommend_from_request(
    players: list[dict[str, Any]],
    *,
    league_size: int,
    scoring: str,
    user_slot: int,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    slot_counts: dict[str, Any] | None = None,
    position_maxes: dict[str, Any] | None = None,
    superflex: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    settings = league_settings_from_request(
        league_size=league_size,
        scoring=scoring,
        roster_size=roster_size,
        roster_template=roster_template,
        slot_counts=slot_counts,
        position_maxes=position_maxes,
        superflex=superflex,
    )
    return recommend_for_team(
        players,
        settings=settings,
        team_slot=user_slot,
        picks=picks,
        debug=debug,
    )
