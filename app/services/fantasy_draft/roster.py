"""Roster construction: optimal lineup, WRT/FLEX, roles, need, imbalance."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.projections import projected_fantasy_points
from app.services.fantasy_draft.settings import LeagueSettings, WRT_ELIGIBLE


def _slot_eligible(slot: str, position: str, settings: LeagueSettings) -> bool:
    su = str(slot).upper()
    pos = str(position or "")
    if su in ("WRT", "FLEX"):
        return pos in WRT_ELIGIBLE
    if su in ("SUPERFLEX", "SF"):
        return pos in settings.superflex_eligible
    if su in ("BENCH", "IR"):
        return True
    return pos == su


def optimize_starting_lineup(
    rostered: list[dict[str, Any]],
    settings: LeagueSettings,
) -> dict[str, Any]:
    """
    Assign players to the strongest legal starting lineup (incl. WRT),
    then remaining to BENCH.
    """
    pool = list(rostered)
    indexed = sorted(
        enumerate(pool),
        key=lambda t: -projected_fantasy_points(t[1], settings.scoring),
    )
    used: set[int] = set()
    ordered_starters: list[dict[str, Any]] = []

    # Pass 1 — dedicated slots only (leave WRT/SF empty for pass 2)
    for slot in settings.starter_slots:
        su = str(slot).upper()
        if su in ("WRT", "FLEX", "SUPERFLEX", "SF"):
            ordered_starters.append(
                {"slot": slot, "player": None, "projected_points": 0.0, "role": "OPEN"}
            )
            continue
        best_j = None
        best_pts = -1.0
        for j, p in indexed:
            if j in used:
                continue
            if not _slot_eligible(slot, str(p.get("position") or ""), settings):
                continue
            pts = projected_fantasy_points(p, settings.scoring)
            if pts > best_pts:
                best_pts = pts
                best_j = j
        if best_j is None:
            ordered_starters.append(
                {"slot": slot, "player": None, "projected_points": 0.0, "role": "OPEN"}
            )
        else:
            used.add(best_j)
            p = pool[best_j]
            ordered_starters.append(
                {
                    "slot": slot,
                    "player": p,
                    "projected_points": projected_fantasy_points(p, settings.scoring),
                    "role": "STARTER",
                }
            )

    # Pass 2 — fill WRT / SUPERFLEX placeholders with best remaining eligible
    for i, row in enumerate(ordered_starters):
        su = str(row["slot"]).upper()
        if su not in ("WRT", "FLEX", "SUPERFLEX", "SF"):
            continue
        best_j = None
        best_pts = -1.0
        for j, p in indexed:
            if j in used:
                continue
            if not _slot_eligible(row["slot"], str(p.get("position") or ""), settings):
                continue
            pts = projected_fantasy_points(p, settings.scoring)
            if pts > best_pts:
                best_pts = pts
                best_j = j
        if best_j is None:
            continue
        used.add(best_j)
        p = pool[best_j]
        role = "WRT" if su in ("WRT", "FLEX") else "SUPERFLEX"
        ordered_starters[i] = {
            "slot": row["slot"],
            "player": p,
            "projected_points": projected_fantasy_points(p, settings.scoring),
            "role": role,
        }

    bench_players: list[dict[str, Any]] = []
    for j, p in indexed:
        if j in used:
            continue
        bench_players.append(p)

    starter_pts = sum(float(r["projected_points"]) for r in ordered_starters)
    starter_filled = sum(1 for r in ordered_starters if r["player"] is not None)
    wrt_total = settings.wrt_slots
    wrt_filled = sum(
        1
        for r in ordered_starters
        if str(r["slot"]).upper() in ("WRT", "FLEX") and r["player"] is not None
    )
    bench_cap = settings.bench_slots
    bench_filled = len(bench_players)

    return {
        "starters": ordered_starters,
        "bench": bench_players[:bench_cap],
        "bench_overflow": bench_players[bench_cap:],
        "open_starter_slots": [
            r["slot"] for r in ordered_starters if r["player"] is None
        ],
        "projected_starter_points": round(starter_pts, 2),
        "counts": {
            "starters_filled": starter_filled,
            "starters_total": len(ordered_starters),
            "wrt_filled": wrt_filled,
            "wrt_total": wrt_total,
            "bench_filled": min(bench_filled, bench_cap),
            "bench_total": bench_cap,
            "roster_filled": starter_filled + min(bench_filled, bench_cap),
            "roster_capacity": settings.roster_capacity,
        },
    }


def compute_open_needs(
    rostered: list[dict[str, Any]],
    settings: LeagueSettings,
) -> list[str]:
    return list(optimize_starting_lineup(rostered, settings)["open_starter_slots"])


def projected_role_for_player(
    rostered: list[dict[str, Any]],
    player: dict[str, Any],
    settings: LeagueSettings,
) -> dict[str, Any]:
    """Where this player would land if drafted now (optimal lineup)."""
    after = optimize_starting_lineup(rostered + [player], settings)
    pid = str(player.get("player_id"))
    open_before = compute_open_needs(rostered, settings)

    for row in after["starters"]:
        p = row.get("player")
        if not p or str(p.get("player_id")) != pid:
            continue
        slot = str(row["slot"]).upper()
        if slot in ("WRT", "FLEX"):
            wrt_i = 0
            for r2 in after["starters"]:
                if str(r2["slot"]).upper() not in ("WRT", "FLEX"):
                    continue
                wrt_i += 1
                if r2.get("player") and str(r2["player"].get("player_id")) == pid:
                    return {
                        "role": "WRT",
                        "slot": row["slot"],
                        "label": f"WRT{wrt_i}",
                        "badge": "WRT STARTER",
                        "is_starter": True,
                        "is_wrt": True,
                        "is_bench": False,
                        "open_starters_remaining": open_before,
                    }
        if slot in ("SUPERFLEX", "SF"):
            return {
                "role": "SUPERFLEX",
                "slot": row["slot"],
                "label": "SUPERFLEX",
                "badge": "STARTER",
                "is_starter": True,
                "is_wrt": False,
                "is_bench": False,
                "open_starters_remaining": open_before,
            }
        same = [
            r
            for r in after["starters"]
            if str(r["slot"]).upper() == slot and r.get("player")
        ]
        idx = 1
        for r in same:
            if str(r["player"].get("player_id")) == pid:
                break
            idx += 1
        multi = sum(1 for s in settings.starter_slots if str(s).upper() == slot) > 1
        filled_need = any(str(s).upper() == slot for s in open_before)
        return {
            "role": "STARTER",
            "slot": row["slot"],
            "label": f"{slot}{idx}" if multi else slot,
            "badge": "STARTER NEED" if filled_need else "STARTER",
            "is_starter": True,
            "is_wrt": False,
            "is_bench": False,
            "open_starters_remaining": open_before,
        }

    bench = after.get("bench") or []
    pos = str(player.get("position") or "WR")
    pos_bench = [p for p in bench if str(p.get("position") or "") == pos]
    idx = 1
    for p in pos_bench:
        if str(p.get("player_id")) == pid:
            break
        idx += 1
    return {
        "role": "BENCH",
        "slot": "BENCH",
        "label": f"BENCH — {pos}{idx}",
        "badge": "BENCH — VALUE OVERRIDE" if open_before else "BENCH VALUE",
        "is_starter": False,
        "is_wrt": False,
        "is_bench": True,
        "open_starters_remaining": open_before,
    }


def _best_lineup_points(
    rostered: list[dict[str, Any]],
    settings: LeagueSettings,
) -> float:
    return float(optimize_starting_lineup(rostered, settings)["projected_starter_points"])


def lineup_impact(
    rostered: list[dict[str, Any]],
    player: dict[str, Any],
    settings: LeagueSettings,
) -> float:
    before = _best_lineup_points(rostered, settings)
    after = _best_lineup_points(rostered + [player], settings)
    return after - before


def flex_lineup_impact(
    rostered: list[dict[str, Any]],
    player: dict[str, Any],
    settings: LeagueSettings,
) -> dict[str, Any]:
    """
    How much this WR/RB/TE would improve WRT starters specifically.

    Compares candidate projected points to the weakest current WRT starter
    (or credits full projection when filling an open WRT). Used so 2×WRT
    leagues correctly value skill-position depth beyond named starters.
    """
    pos = str(player.get("position") or "")
    empty = {
        "delta": 0.0,
        "candidate_pts": 0.0,
        "wrt_floor": 0.0,
        "open_wrt": 0,
        "would_start_wrt": False,
        "eligible": False,
    }
    if pos not in WRT_ELIGIBLE or settings.wrt_slots <= 0:
        return empty

    cand_pts = projected_fantasy_points(player, settings.scoring)
    before = optimize_starting_lineup(rostered, settings)
    after = optimize_starting_lineup(rostered + [player], settings)
    pid = str(player.get("player_id"))

    wrt_before = [
        r
        for r in before["starters"]
        if str(r["slot"]).upper() in ("WRT", "FLEX")
    ]
    open_wrt = sum(1 for r in wrt_before if r.get("player") is None)
    filled_pts = [
        float(r["projected_points"])
        for r in wrt_before
        if r.get("player") is not None
    ]
    wrt_floor = min(filled_pts) if filled_pts else 0.0

    would_start_wrt = any(
        str(r["slot"]).upper() in ("WRT", "FLEX")
        and r.get("player")
        and str(r["player"].get("player_id")) == pid
        for r in after["starters"]
    )

    if would_start_wrt:
        # Points this player contributes in a WRT slot after assignment
        after_pts = next(
            (
                float(r["projected_points"])
                for r in after["starters"]
                if str(r["slot"]).upper() in ("WRT", "FLEX")
                and r.get("player")
                and str(r["player"].get("player_id")) == pid
            ),
            cand_pts,
        )
        if open_wrt > 0:
            delta = after_pts  # filling an empty WRT
        else:
            delta = max(0.0, after_pts - wrt_floor)
    else:
        # Started at dedicated RB/WR/TE — measure WRT pool upgrade indirectly
        before_wrt_pts = sum(float(r["projected_points"]) for r in wrt_before)
        after_wrt_pts = sum(
            float(r["projected_points"])
            for r in after["starters"]
            if str(r["slot"]).upper() in ("WRT", "FLEX")
        )
        delta = max(0.0, after_wrt_pts - before_wrt_pts)

    return {
        "delta": round(delta, 2),
        "candidate_pts": round(cand_pts, 2),
        "wrt_floor": round(wrt_floor, 2),
        "open_wrt": open_wrt,
        "would_start_wrt": would_start_wrt,
        "eligible": True,
    }


def starter_need_urgency(
    open_needs: list[str],
    *,
    settings: LeagueSettings,
    draft_progress: float,
    remaining_team_picks: int,
    available: list[dict[str, Any]] | None = None,
    position: str | None = None,
) -> float:
    """
    How dangerous it is to leave starter holes unfilled.

    Not round-number alone — also remaining team picks, hole count, and
    (when available) quality left at the relevant position.
    """
    holes = [str(s).upper() for s in open_needs if str(s).upper() not in ("BENCH", "IR")]
    if not holes:
        return 0.15  # residual depth urgency only

    progress = max(0.0, min(1.0, draft_progress))
    hole_pressure = min(1.0, 0.22 * len(holes) + 0.12 * sum(
        1 for h in holes if h not in ("WRT", "FLEX", "SUPERFLEX", "SF")
    ))

    # Running out of picks while holes remain → urgency spikes
    picks_left = max(0, int(remaining_team_picks))
    if picks_left <= len(holes):
        pick_pressure = 1.0
    elif picks_left <= len(holes) + 2:
        pick_pressure = 0.85
    else:
        # fraction of remaining picks that must cover holes
        pick_pressure = min(1.0, len(holes) / max(1, picks_left) * 1.4)

    quality_pressure = 0.35
    if available is not None and position:
        pos = str(position)
        relevant = []
        for p in available:
            ppos = str(p.get("position") or "")
            if ppos == pos:
                relevant.append(projected_fantasy_points(p, settings.scoring))
            elif pos in ("WRT", "FLEX") and ppos in WRT_ELIGIBLE:
                relevant.append(projected_fantasy_points(p, settings.scoring))
        relevant.sort(reverse=True)
        if not relevant:
            quality_pressure = 1.0
        else:
            # Thin / weak remaining pool → higher urgency
            top = relevant[0]
            depth = relevant[min(4, len(relevant) - 1)]
            cliff = top - depth
            if top < 140:
                quality_pressure = 0.9
            elif cliff < 15 and len(relevant) < 8:
                quality_pressure = 0.75
            elif len(relevant) < settings.league_size:
                quality_pressure = 0.65
            else:
                quality_pressure = 0.35 + 0.25 * progress

    urgency = (
        0.25 * progress
        + 0.30 * hole_pressure
        + 0.25 * pick_pressure
        + 0.20 * quality_pressure
    )
    return max(0.2, min(1.0, urgency))


def roster_need_score(
    player: dict[str, Any],
    open_needs: list[str],
    settings: LeagueSettings,
    *,
    draft_progress: float,
    rostered: list[dict[str, Any]] | None = None,
    available: list[dict[str, Any]] | None = None,
    remaining_team_picks: int | None = None,
) -> float:
    """
    Need fit for this player given the team's *current* open starter slots.

    Recomputed every recommendation from the live roster — as picks land,
    open_needs shrink and this score shifts automatically.
    """
    pos = str(player.get("position") or "")
    norm_needs = [str(s).upper() for s in open_needs]
    open_wrt = sum(1 for s in norm_needs if s in ("WRT", "FLEX"))
    has_dedicated = pos in norm_needs
    rem = (
        remaining_team_picks
        if remaining_team_picks is not None
        else max(1, settings.rounds - (len(rostered) if rostered else 0))
    )

    urgency = starter_need_urgency(
        open_needs,
        settings=settings,
        draft_progress=draft_progress,
        remaining_team_picks=rem,
        available=available,
        position=pos if has_dedicated else ("WRT" if open_wrt and pos in WRT_ELIGIBLE else pos),
    )

    if has_dedicated:
        base = 0.88 + 0.12 * urgency
    elif open_wrt and pos in WRT_ELIGIBLE:
        wrt_weight = 0.68 + 0.08 * min(3, open_wrt) + 0.04 * min(2, settings.wrt_slots)
        if rostered is not None:
            flex = flex_lineup_impact(rostered, player, settings)
            if flex["delta"] >= 40:
                wrt_weight = max(wrt_weight, 0.90)
            elif flex["delta"] >= 20:
                wrt_weight = max(wrt_weight, 0.82)
            elif flex["open_wrt"] == 0 and flex["delta"] < 5:
                wrt_weight = min(wrt_weight, 0.50)
        base = min(0.94, wrt_weight) * (0.75 + 0.25 * urgency)
    elif any(s in ("SUPERFLEX", "SF") for s in norm_needs) and pos in settings.superflex_eligible:
        base = 0.80 * (0.75 + 0.25 * urgency)
    elif pos in WRT_ELIGIBLE and settings.wrt_slots >= 2 and rostered is not None:
        flex = flex_lineup_impact(rostered, player, settings)
        if flex["delta"] >= 25:
            base = 0.48
        elif flex["delta"] >= 12:
            base = 0.32
        else:
            base = 0.12
    else:
        # Does not fill a current hole — low need, especially early with open starters
        hole_count = len([s for s in norm_needs if s not in ("BENCH", "IR")])
        if hole_count >= 3:
            base = 0.08
        elif hole_count >= 1:
            base = 0.12
        else:
            base = 0.22  # starters full — depth is legitimate

    # When many holes remain and few picks left, non-fits get crushed harder
    if not has_dedicated and not (open_wrt and pos in WRT_ELIGIBLE):
        if len(norm_needs) >= 2 and rem <= len(norm_needs) + 1:
            base *= 0.45

    return max(0.0, min(1.0, base))


def roster_imbalance_penalty(
    rostered: list[dict[str, Any]],
    player: dict[str, Any],
    settings: LeagueSettings,
    open_needs: list[str],
) -> float:
    from app.services.fantasy_draft.eligibility import count_by_position

    pos = str(player.get("position") or "")
    counts = count_by_position(rostered)
    n = counts.get(pos, 0) + 1

    dedicated = sum(1 for s in settings.starter_slots if s == pos)
    # 2 WRT slots meaningfully raise comfortable RB/WR/TE depth
    wrt_room = settings.wrt_slots if pos in WRT_ELIGIBLE else 0
    sflex_room = (
        int(settings.slot_counts.get("SUPERFLEX", 0) or 0)
        if pos in settings.superflex_eligible
        else 0
    )
    comfortable = dedicated + wrt_room + sflex_room + max(1, settings.bench_slots // 3)

    if n <= comfortable:
        return 0.0

    # Don't treat WRT-open as a "hole" against drafting another WRT-eligible
    holes = [
        h
        for h in open_needs
        if str(h).upper() not in (pos, "BENCH", "IR", "WRT", "FLEX")
        or (
            str(h).upper() in ("WRT", "FLEX")
            and pos not in WRT_ELIGIBLE
        )
    ]
    # Soften penalty when WRT still open and this player can fill it
    if any(str(h).upper() in ("WRT", "FLEX") for h in open_needs) and pos in WRT_ELIGIBLE:
        return min(0.35, 0.08 * (n - comfortable))

    if not holes:
        return min(0.55, 0.1 * (n - comfortable))
    return min(1.0, 0.16 * (n - comfortable) + 0.1 * len(holes))
