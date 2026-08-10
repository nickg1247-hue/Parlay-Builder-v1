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


def roster_need_score(
    player: dict[str, Any],
    open_needs: list[str],
    settings: LeagueSettings,
    *,
    draft_progress: float,
) -> float:
    pos = str(player.get("position") or "")
    norm_needs = [str(s).upper() for s in open_needs]
    if pos in norm_needs:
        base = 1.0
    elif any(s in ("WRT", "FLEX") for s in norm_needs) and pos in WRT_ELIGIBLE:
        base = 0.78
    elif any(s in ("SUPERFLEX", "SF") for s in norm_needs) and pos in settings.superflex_eligible:
        base = 0.82
    else:
        base = 0.18
    phase = 0.45 + 0.55 * max(0.0, min(1.0, draft_progress))
    return max(0.0, min(1.0, base * phase))


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
    wrt_room = settings.wrt_slots if pos in WRT_ELIGIBLE else 0
    sflex_room = (
        int(settings.slot_counts.get("SUPERFLEX", 0) or 0)
        if pos in settings.superflex_eligible
        else 0
    )
    comfortable = dedicated + wrt_room + sflex_room + max(1, settings.bench_slots // 3)

    if n <= comfortable:
        return 0.0

    holes = [h for h in open_needs if str(h).upper() not in (pos, "BENCH", "IR")]
    if not holes:
        return min(0.55, 0.1 * (n - comfortable))
    return min(1.0, 0.16 * (n - comfortable) + 0.1 * len(holes))
