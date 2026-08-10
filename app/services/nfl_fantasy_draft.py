"""NFL redraft snake-draft helper: rankings load, roster needs, recommend scoring."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

RANKINGS_PATH = (
    PROJECT_ROOT / "data" / "processed" / "nfl_fantasy_rankings_2026.json"
)

DEFAULT_ROSTER_TEMPLATE: list[str] = [
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "DST",
    "K",
]

# Per-team position caps (typical ESPN/Yahoo defaults). Configurable per league.
DEFAULT_POSITION_MAXES: dict[str, int] = {
    "QB": 4,
    "RB": 8,
    "WR": 8,
    "TE": 3,
    "DST": 3,
    "K": 3,
}

DEFAULT_ROSTER_SIZE = 15  # picks per team (snake rounds)

FLEX_ELIGIBLE = frozenset({"RB", "WR", "TE"})
POSITION_KEYS = ("QB", "RB", "WR", "TE", "DST", "K")

SCORING_RANK_KEY = {
    "standard": "rank_std",
    "half_ppr": "rank_half",
    "ppr": "rank_ppr",
}

# Recommend weights (plain English in DEV.md)
BASE_RANK_SPAN = 250.0
NEED_BONUS_MIN = 10.0
NEED_BONUS_MAX = 48.0
SCARCITY_MAX = 22.0
BYE_PENALTY = 4.0
STARTER_QUALITY_MULT = {
    "QB": 1.2,
    "RB": 2.6,
    "WR": 2.6,
    "TE": 1.3,
    "DST": 1.1,
    "K": 1.1,
}


def team_slot_for_overall(overall: int, league_size: int) -> int:
    """Snake: odd rounds 1..N, even rounds N..1."""
    if overall < 1 or league_size < 1:
        raise ValueError("overall and league_size must be >= 1")
    round_num = (overall - 1) // league_size + 1
    pick_in_round = (overall - 1) % league_size + 1
    if round_num % 2 == 1:
        return pick_in_round
    return league_size - pick_in_round + 1


def round_for_overall(overall: int, league_size: int) -> int:
    return (overall - 1) // league_size + 1


def snake_draft_order(league_size: int, rounds: int) -> list[dict[str, int]]:
    """Full snake board: overall, round, team_slot for each pick."""
    order: list[dict[str, int]] = []
    total = league_size * rounds
    for overall in range(1, total + 1):
        order.append(
            {
                "overall": overall,
                "round": round_for_overall(overall, league_size),
                "team_slot": team_slot_for_overall(overall, league_size),
            }
        )
    return order


def next_overall_after(picks: list[dict[str, Any]]) -> int:
    if not picks:
        return 1
    return max(int(p["overall"]) for p in picks) + 1


def undo_last_pick(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove the pick with the highest overall number (pure)."""
    if not picks:
        return []
    max_o = max(int(p["overall"]) for p in picks)
    out: list[dict[str, Any]] = []
    removed = False
    for p in reversed(picks):
        if not removed and int(p["overall"]) == max_o:
            removed = True
            continue
        out.append(p)
    out.reverse()
    return out


def drafted_ids(picks: list[dict[str, Any]]) -> set[str]:
    return {str(p["player_id"]) for p in picks}


def available_players(
    players: list[dict[str, Any]], picks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    taken = drafted_ids(picks)
    return [p for p in players if str(p["player_id"]) not in taken]


def rank_for_scoring(player: dict[str, Any], scoring: str) -> int:
    key = SCORING_RANK_KEY.get(scoring, SCORING_RANK_KEY["half_ppr"])
    raw = player.get(key)
    if raw is None:
        raw = player.get("adp") or player.get("rank_half") or 999
    return int(raw)


def normalize_scoring(scoring: str | None) -> str:
    s = (scoring or "half_ppr").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("std", "standard", "non_ppr"):
        return "standard"
    if s in ("half", "half_ppr", "halfppr"):
        return "half_ppr"
    if s in ("ppr", "full_ppr", "fullppr"):
        return "ppr"
    return "half_ppr"


def normalize_position_maxes(
    maxes: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Merge league maxes onto defaults; values clamped to 0–20."""
    out = dict(DEFAULT_POSITION_MAXES)
    if not maxes:
        return out
    for key in POSITION_KEYS:
        if key not in maxes and key.lower() not in {k.lower() for k in maxes}:
            continue
        raw = maxes.get(key, maxes.get(key.lower()))
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        out[key] = max(0, min(20, val))
    return out


def normalize_roster_size(
    roster_size: int | None,
    starter_count: int,
) -> int:
    size = int(roster_size) if roster_size is not None else DEFAULT_ROSTER_SIZE
    size = max(starter_count, min(18, size))
    return size


def resolve_roster_template(
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
) -> tuple[list[str], list[str], int]:
    """Return (full_template with BENCH, starter_slots_only, roster_size)."""
    starters = [
        s
        for s in (roster_template or DEFAULT_ROSTER_TEMPLATE)
        if str(s).upper() != "BENCH"
    ]
    if not starters:
        starters = list(DEFAULT_ROSTER_TEMPLATE)
    size = normalize_roster_size(roster_size, len(starters))
    full = list(starters) + ["BENCH"] * (size - len(starters))
    return full, starters, size


def count_by_position(rostered: list[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in POSITION_KEYS}
    for p in rostered:
        pos = str(p.get("position") or "")
        if pos in counts:
            counts[pos] += 1
    return counts


def can_add_position(
    rostered: list[dict[str, Any]],
    position: str,
    position_maxes: dict[str, int] | None = None,
) -> bool:
    """True if adding this position would not exceed the league max for the team."""
    maxes = normalize_position_maxes(position_maxes)
    pos = str(position or "")
    if pos not in maxes:
        return True
    return count_by_position(rostered).get(pos, 0) < maxes[pos]


def team_roster_players(
    players_by_id: dict[str, dict[str, Any]],
    picks: list[dict[str, Any]],
    team_slot: int,
) -> list[dict[str, Any]]:
    return [
        players_by_id[str(p["player_id"])]
        for p in picks
        if int(p["team_slot"]) == team_slot and str(p["player_id"]) in players_by_id
    ]


def compute_open_needs(
    rostered: list[dict[str, Any]],
    template: list[str] | None = None,
) -> list[str]:
    """Remaining unfilled starter/flex slots (BENCH ignored)."""
    slots = [
        s
        for s in (template or DEFAULT_ROSTER_TEMPLATE)
        if str(s).upper() != "BENCH"
    ]
    remaining: list[str | None] = list(slots)
    used = [False] * len(rostered)

    for i, slot in enumerate(remaining):
        if slot == "FLEX":
            continue
        for j, p in enumerate(rostered):
            if used[j]:
                continue
            if p.get("position") == slot:
                used[j] = True
                remaining[i] = None
                break

    for i, slot in enumerate(remaining):
        if slot != "FLEX":
            continue
        for j, p in enumerate(rostered):
            if used[j]:
                continue
            if p.get("position") in FLEX_ELIGIBLE:
                used[j] = True
                remaining[i] = None
                break

    return [s for s in remaining if s is not None]


def player_fills_need(position: str, open_needs: list[str]) -> tuple[bool, str]:
    if position in open_needs:
        return True, position
    if "FLEX" in open_needs and position in FLEX_ELIGIBLE:
        return True, "FLEX"
    return False, ""


def user_next_overall(
    league_size: int, user_slot: int, current_overall: int, total_picks: int
) -> int | None:
    for overall in range(current_overall, total_picks + 1):
        if team_slot_for_overall(overall, league_size) == user_slot:
            return overall
    return None


def _starter_quality_threshold(position: str, league_size: int) -> float:
    mult = STARTER_QUALITY_MULT.get(position, 1.5)
    return league_size * mult


def _scarcity_bonus(
    position: str,
    available: list[dict[str, Any]],
    *,
    league_size: int,
    scoring: str,
    picks_until_user: int,
) -> float:
    thresh = _starter_quality_threshold(position, league_size)
    remaining = sum(
        1
        for p in available
        if p.get("position") == position
        and rank_for_scoring(p, scoring) <= thresh
    )
    if remaining <= 0:
        return SCARCITY_MAX * 0.35
    # Rough demand from other managers before you're up
    demand = max(picks_until_user, 0) * 0.4 + 0.5
    ratio = demand / remaining
    return min(SCARCITY_MAX, SCARCITY_MAX * min(ratio, 2.0) / 2.0)


def _need_bonus_weight(
    *,
    open_needs: list[str],
    user_next: int,
    total_picks: int,
    user_picks_left: int,
) -> float:
    """Small early when needs are wide open; larger as rounds progress with holes left."""
    holes = len(open_needs)
    if holes <= 0:
        return 0.0
    progress = (user_next - 1) / max(total_picks - 1, 1)
    open_frac = holes / max(len(DEFAULT_ROSTER_TEMPLATE), 1)
    # Wide-open early → keep near MIN so BPA wins
    bpa_bias = max(0.0, open_frac - 0.55)  # only when >~half slots still open
    pressure = holes / max(user_picks_left, 1)
    t = progress * (1.0 - 0.55 * bpa_bias) * min(max(pressure, 0.4), 2.0) / 1.2
    t = max(0.0, min(1.0, t))
    return NEED_BONUS_MIN + (NEED_BONUS_MAX - NEED_BONUS_MIN) * t


def _bye_collision(
    player: dict[str, Any], rostered: list[dict[str, Any]]
) -> bool:
    bye = player.get("bye")
    if bye is None:
        return False
    for p in rostered:
        if p.get("bye") == bye and p.get("position") in {
            "QB",
            "RB",
            "WR",
            "TE",
            "FLEX",
            "DST",
            "K",
        }:
            return True
    return False


def power_rank_from_consensus(rank: int) -> int:
    """1–100 power rating from consensus rank (1 = elite)."""
    return int(max(1, min(100, 101 - int(rank))))


def fit_pct_from_scores(score: float, top_score: float) -> int:
    """How good this pick is for *you* vs the current best available (40–99)."""
    if top_score <= 0:
        return 50
    ratio = max(0.0, min(1.0, score / top_score))
    return int(round(40 + ratio * 59))


def score_player(
    player: dict[str, Any],
    *,
    scoring: str,
    open_needs: list[str],
    rostered: list[dict[str, Any]],
    available: list[dict[str, Any]],
    league_size: int,
    need_weight: float,
    picks_until_user: int,
    position_maxes: dict[str, int] | None = None,
) -> tuple[float, list[str]]:
    rank = rank_for_scoring(player, scoring)
    base = BASE_RANK_SPAN + 1.0 - float(rank)
    reasons: list[str] = []
    pos = str(player.get("position") or "")

    if not can_add_position(rostered, pos, position_maxes):
        # Illegal under league max — bury so it never wins primary
        return -10_000.0, [f"At {pos} max"]

    fills, slot = player_fills_need(pos, open_needs)
    need = need_weight if fills else 0.0
    if fills:
        reasons.append(f"Fills {slot}" if slot != pos else f"Fills {slot} need")

    scarce = _scarcity_bonus(
        pos,
        available,
        league_size=league_size,
        scoring=scoring,
        picks_until_user=picks_until_user,
    )
    if scarce >= SCARCITY_MAX * 0.45:
        reasons.append(f"Scarce at {pos}")

    bye_pen = 0.0
    if _bye_collision(player, rostered):
        bye_pen = BYE_PENALTY
        reasons.append("Bye stack risk")

    if not reasons and rank <= league_size:
        reasons.append("Board value")

    total = base + need + scarce - bye_pen
    return total, reasons[:3]


def evaluate_player(
    players: list[dict[str, Any]],
    *,
    player_id: str,
    league_size: int,
    scoring: str,
    user_slot: int,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    position_maxes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full insight card for one player in the current draft context."""
    scoring = normalize_scoring(scoring)
    maxes = normalize_position_maxes(position_maxes)
    rec = recommend(
        players,
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        position_maxes=maxes,
        alternate_count=5,
    )
    by_id = {str(p["player_id"]): p for p in players}
    player = by_id.get(str(player_id))
    if player is None:
        raise ValueError("Unknown player_id")

    rank = rank_for_scoring(player, scoring)
    drafted = str(player_id) in drafted_ids(picks)
    pool_hit = next(
        (row for row in (rec.get("top_pool") or []) if row["player_id"] == player_id),
        None,
    )
    full_template, starters, size = resolve_roster_template(
        roster_template, roster_size
    )
    user_rostered = team_roster_players(by_id, picks, user_slot)
    current = next_overall_after(picks)
    total_picks = league_size * size
    on_clock = (
        team_slot_for_overall(current, league_size) if current <= total_picks else None
    )
    clock_rostered = (
        team_roster_players(by_id, picks, on_clock) if on_clock is not None else []
    )
    pos = str(player.get("position") or "")
    legal_for_user = can_add_position(user_rostered, pos, maxes)
    legal_for_clock = (
        can_add_position(clock_rostered, pos, maxes) if on_clock is not None else True
    )

    # If outside top_pool, re-score against same context
    if pool_hit is None and not drafted:
        open_needs = compute_open_needs(user_rostered, starters)
        avail = available_players(players, picks)
        user_next = user_next_overall(league_size, user_slot, current, total_picks) or current
        picks_until_user = max(user_next - current, 0)
        user_picks_left = sum(
            1
            for o in range(user_next, total_picks + 1)
            if team_slot_for_overall(o, league_size) == user_slot
        )
        need_weight = _need_bonus_weight(
            open_needs=open_needs,
            user_next=user_next,
            total_picks=total_picks,
            user_picks_left=max(user_picks_left, 1),
        )
        total, reasons = score_player(
            player,
            scoring=scoring,
            open_needs=open_needs,
            rostered=user_rostered,
            available=avail,
            league_size=league_size,
            need_weight=need_weight,
            picks_until_user=picks_until_user,
            position_maxes=maxes,
        )
        top = float((rec.get("primary") or {}).get("score") or max(total, 1.0))
        pool_hit = {
            "player_id": player_id,
            "score": round(total, 3),
            "fit_pct": fit_pct_from_scores(total, top) if total > -1000 else 40,
            "reasons": reasons,
        }

    pick_meta = next((p for p in picks if str(p["player_id"]) == str(player_id)), None)
    reasons = [] if drafted else list((pool_hit or {}).get("reasons") or [])
    if not drafted and not legal_for_user and "At" not in " ".join(reasons):
        reasons = [f"At {pos} max"] + reasons

    return {
        "player": {
            "player_id": player["player_id"],
            "name": player["name"],
            "position": player["position"],
            "team": player.get("team"),
            "bye": player.get("bye"),
            "tier": player.get("tier"),
            "rank": rank,
            "power_rank": power_rank_from_consensus(rank),
            "adp": player.get("adp"),
            "projected_pick": round(float(player.get("adp") or rank), 1),
        },
        "drafted": drafted,
        "pick": pick_meta,
        "fit_pct": None if drafted else (pool_hit or {}).get("fit_pct"),
        "score": None if drafted else (pool_hit or {}).get("score"),
        "reasons": reasons[:3],
        "legal_for_user": legal_for_user,
        "legal_for_clock_team": legal_for_clock,
        "position_maxes": maxes,
        "user_position_counts": count_by_position(user_rostered),
        "clock_position_counts": count_by_position(clock_rostered),
        "primary": rec.get("primary"),
        "board_meta": rec.get("board_meta"),
        "roster_template_full": full_template,
    }


def recommend(
    players: list[dict[str, Any]],
    *,
    league_size: int,
    scoring: str,
    user_slot: int,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    position_maxes: dict[str, Any] | None = None,
    alternate_count: int = 3,
) -> dict[str, Any]:
    """Recommend for the USER's next pick; board_meta includes on-clock state."""
    scoring = normalize_scoring(scoring)
    maxes = normalize_position_maxes(position_maxes)
    full_template, starters, size = resolve_roster_template(
        roster_template, roster_size
    )
    if league_size < 8 or league_size > 14:
        raise ValueError("league_size must be 8–14")
    if user_slot < 1 or user_slot > league_size:
        raise ValueError("user_slot must be 1..league_size")

    total_picks = league_size * size
    current = next_overall_after(picks)
    on_clock = (
        team_slot_for_overall(current, league_size) if current <= total_picks else None
    )
    user_next = user_next_overall(league_size, user_slot, current, total_picks)
    user_on_clock = on_clock == user_slot and current <= total_picks

    by_id = {str(p["player_id"]): p for p in players}
    user_rostered = team_roster_players(by_id, picks, user_slot)
    open_needs = compute_open_needs(user_rostered, starters)
    avail = available_players(players, picks)
    user_counts = count_by_position(user_rostered)
    clock_counts = (
        count_by_position(team_roster_players(by_id, picks, on_clock))
        if on_clock is not None
        else {k: 0 for k in POSITION_KEYS}
    )

    meta_base = {
        "current_overall": current if current <= total_picks else None,
        "on_clock_slot": on_clock,
        "user_on_clock": bool(user_on_clock),
        "user_next_overall": user_next,
        "user_needs": open_needs,
        "scoring": scoring,
        "league_size": league_size,
        "total_picks": total_picks,
        "picks_made": len(picks),
        "roster_size": size,
        "position_maxes": maxes,
        "user_position_counts": user_counts,
        "clock_position_counts": clock_counts,
        "starter_template": starters,
    }

    legal_avail = [
        p for p in avail if can_add_position(user_rostered, str(p.get("position") or ""), maxes)
    ]

    if user_next is None or not legal_avail:
        return {
            "primary": None,
            "alternates": [],
            "top_pool": [],
            "board_meta": {**meta_base, "user_on_clock": False if user_next is None else meta_base["user_on_clock"]},
        }

    picks_until_user = max(user_next - current, 0)
    user_picks_left = sum(
        1
        for o in range(user_next, total_picks + 1)
        if team_slot_for_overall(o, league_size) == user_slot
    )
    need_weight = _need_bonus_weight(
        open_needs=open_needs,
        user_next=user_next,
        total_picks=total_picks,
        user_picks_left=user_picks_left,
    )

    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for p in legal_avail:
        total, reasons = score_player(
            p,
            scoring=scoring,
            open_needs=open_needs,
            rostered=user_rostered,
            available=avail,
            league_size=league_size,
            need_weight=need_weight,
            picks_until_user=picks_until_user,
            position_maxes=maxes,
        )
        scored.append((total, p, reasons))

    scored.sort(key=lambda t: (-t[0], rank_for_scoring(t[1], scoring), t[1]["name"]))

    top_score = scored[0][0] if scored else 0.0

    def pack(row: tuple[float, dict[str, Any], list[str]]) -> dict[str, Any]:
        total, p, reasons = row
        rank = rank_for_scoring(p, scoring)
        return {
            "player_id": p["player_id"],
            "name": p["name"],
            "position": p["position"],
            "team": p.get("team"),
            "bye": p.get("bye"),
            "tier": p.get("tier"),
            "rank": rank,
            "power_rank": power_rank_from_consensus(rank),
            "adp": p.get("adp"),
            "projected_pick": round(float(p.get("adp") or rank), 1),
            "score": round(total, 3),
            "fit_pct": fit_pct_from_scores(total, top_score),
            "reasons": reasons,
        }

    primary = pack(scored[0]) if scored else None
    alts = [pack(r) for r in scored[1 : 1 + max(0, alternate_count)]]
    top_pool = [pack(r) for r in scored[:40]]

    return {
        "primary": primary,
        "alternates": alts,
        "top_pool": top_pool,
        "board_meta": meta_base,
        "roster_template_full": full_template,
    }


@lru_cache(maxsize=1)
def _load_rankings_raw(path_str: str, mtime_ns: int) -> dict[str, Any]:
    path = Path(path_str)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_rankings(path: Path | None = None) -> dict[str, Any]:
    p = path or RANKINGS_PATH
    if not p.exists():
        raise FileNotFoundError(f"Rankings file not found: {p}")
    stat = p.stat()
    return _load_rankings_raw(str(p.resolve()), stat.st_mtime_ns)


def list_players_for_api(scoring: str | None = None) -> dict[str, Any]:
    scoring = normalize_scoring(scoring)
    data = load_rankings()
    players_out: list[dict[str, Any]] = []
    for p in data.get("players") or []:
        rank = rank_for_scoring(p, scoring)
        players_out.append(
            {
                "player_id": p["player_id"],
                "name": p["name"],
                "position": p["position"],
                "team": p.get("team"),
                "bye": p.get("bye"),
                "adp": p.get("adp"),
                "tier": p.get("tier"),
                "rank_std": p.get("rank_std"),
                "rank_half": p.get("rank_half"),
                "rank_ppr": p.get("rank_ppr"),
                "rank": rank,
                "power_rank": power_rank_from_consensus(rank),
                "projected_pick": round(float(p.get("adp") or rank), 1),
            }
        )
    players_out.sort(key=lambda x: (x["rank"], x["name"]))
    return {
        "season": data.get("season"),
        "source": data.get("source"),
        "scoring": scoring,
        "count": len(players_out),
        "players": players_out,
        "roster_template": list(DEFAULT_ROSTER_TEMPLATE),
        "default_roster_size": DEFAULT_ROSTER_SIZE,
        "default_position_maxes": dict(DEFAULT_POSITION_MAXES),
    }


def recommend_from_board(
    *,
    league_size: int,
    scoring: str,
    user_slot: int,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    position_maxes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = load_rankings()
    return recommend(
        list(data.get("players") or []),
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        position_maxes=position_maxes,
    )


def evaluate_player_from_board(
    *,
    player_id: str,
    league_size: int,
    scoring: str,
    user_slot: int,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    position_maxes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = load_rankings()
    return evaluate_player(
        list(data.get("players") or []),
        player_id=player_id,
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        position_maxes=position_maxes,
    )
