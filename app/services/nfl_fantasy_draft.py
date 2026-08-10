"""NFL fantasy draft — compatibility facade over fantasy_draft engine.

Snake helpers + rankings I/O live here for existing imports/tests.
Recommendation scoring is delegated to app.services.fantasy_draft (ERVA engine).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.fantasy_draft.eligibility import (
    available_pool,
    can_team_draft_player,
    count_by_position,
    get_eligible_players,
    team_roster_from_picks,
    validate_pick,
)
from app.services.fantasy_draft.engine import (
    apply_pick,
    recommend_from_request,
)
from app.services.fantasy_draft.projections import (
    power_rank_from_consensus,
    projected_fantasy_points,
    rank_for_scoring,
)
from app.services.fantasy_draft.settings import (
    DEFAULT_POSITION_MAXES,
    DEFAULT_ROSTER_SIZE,
    DEFAULT_SLOT_COUNTS,
    DEFAULT_STARTER_TEMPLATE,
    FLEX_ELIGIBLE,
    POSITION_KEYS,
    LeagueSettings,
    league_settings_from_request,
    normalize_position_maxes,
    normalize_scoring,
)

# Back-compat aliases
DEFAULT_ROSTER_TEMPLATE = list(DEFAULT_STARTER_TEMPLATE)
RANKINGS_PATH = PROJECT_ROOT / "data" / "processed" / "nfl_fantasy_rankings_2026.json"


def team_slot_for_overall(overall: int, league_size: int) -> int:
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
    return available_pool(players, picks)


def can_add_position(
    rostered: list[dict[str, Any]],
    position: str,
    position_maxes: dict[str, int] | None = None,
) -> bool:
    settings = league_settings_from_request(
        league_size=10,
        position_maxes=position_maxes,
    )
    return can_team_draft_player(
        rostered, {"position": position, "player_id": "_"}, settings
    )


def resolve_roster_template(
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
) -> tuple[list[str], list[str], int]:
    settings = league_settings_from_request(
        league_size=10,
        roster_template=roster_template,
        roster_size=roster_size,
    )
    full = settings.full_template
    starters = list(settings.starter_slots)
    return full, starters, settings.rounds


def compute_open_needs(
    rostered: list[dict[str, Any]],
    template: list[str] | None = None,
) -> list[str]:
    from app.services.fantasy_draft.roster import compute_open_needs as _needs

    settings = league_settings_from_request(
        league_size=10,
        roster_template=template or DEFAULT_ROSTER_TEMPLATE,
    )
    return _needs(rostered, settings)


def player_fills_need(position: str, open_needs: list[str]) -> tuple[bool, str]:
    norm = [str(s).upper() for s in open_needs]
    if position in norm:
        return True, position
    if any(s in ("WRT", "FLEX") for s in norm) and position in FLEX_ELIGIBLE:
        label = "WRT" if "WRT" in norm else "FLEX"
        return True, label
    if "SUPERFLEX" in norm and position in {"QB", "RB", "WR", "TE"}:
        return True, "SUPERFLEX"
    return False, ""


def fit_pct_from_scores(score: float, top_score: float) -> int:
    if top_score <= 0:
        return 50
    ratio = max(0.0, min(1.0, score / top_score))
    return int(round(40 + ratio * 59))


def recommend(
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
    alternate_count: int = 3,
    superflex: bool = False,
    debug: bool = False,
) -> dict[str, Any]:
    result = recommend_from_request(
        players,
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        slot_counts=slot_counts,
        position_maxes=position_maxes,
        superflex=superflex,
        debug=debug,
    )
    # Trim alternates to requested count
    alts = list(result.get("alternates") or [])[: max(0, alternate_count)]
    result["alternates"] = alts
    # Legacy primary shape: ensure reasons present
    return result


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
    slot_counts: dict[str, Any] | None = None,
    position_maxes: dict[str, Any] | None = None,
    superflex: bool = False,
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
    players_by_id = {str(p["player_id"]): p for p in players}
    player = players_by_id.get(str(player_id))
    if player is None:
        raise ValueError("Unknown player_id")

    rec = recommend(
        players,
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        slot_counts=slot_counts,
        position_maxes=position_maxes,
        superflex=superflex,
        alternate_count=5,
        debug=True,
    )
    drafted = str(player_id) in drafted_ids(picks)
    pool_hit = next(
        (row for row in (rec.get("top_pool") or []) if row["player_id"] == player_id),
        None,
    )
    current = next_overall_after(picks)
    on_clock = (
        team_slot_for_overall(current, settings.league_size)
        if current <= settings.total_picks
        else None
    )
    user_rostered = team_roster_from_picks(picks, players_by_id, user_slot)
    clock_rostered = (
        team_roster_from_picks(picks, players_by_id, on_clock) if on_clock else []
    )
    legal_for_user = can_team_draft_player(user_rostered, player, settings)
    legal_for_clock = (
        can_team_draft_player(clock_rostered, player, settings) if on_clock else True
    )
    rank = rank_for_scoring(player, settings.scoring)
    pick_meta = next((p for p in picks if str(p["player_id"]) == str(player_id)), None)

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
            "projected_points": projected_fantasy_points(player, settings.scoring),
        },
        "drafted": drafted,
        "pick": pick_meta,
        "fit_pct": None if drafted else (pool_hit or {}).get("fit_pct"),
        "score": None if drafted else (pool_hit or {}).get("score"),
        "reasons": [] if drafted else list((pool_hit or {}).get("reasons") or []),
        "why_not": None if drafted else (pool_hit or {}).get("why_not"),
        "components": None if drafted else (pool_hit or {}).get("components"),
        "vorp": None if drafted else (pool_hit or {}).get("vorp"),
        "p_available_next": None if drafted else (pool_hit or {}).get("p_available_next"),
        "projected_role": None if drafted else (pool_hit or {}).get("projected_role"),
        "legal_for_user": legal_for_user,
        "legal_for_clock_team": legal_for_clock,
        "position_maxes": dict(settings.position_maxes),
        "user_position_counts": count_by_position(user_rostered),
        "clock_position_counts": count_by_position(clock_rostered),
        "primary": rec.get("primary"),
        "board_meta": rec.get("board_meta"),
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
                "projected_points": projected_fantasy_points(p, scoring),
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
        "default_slot_counts": dict(DEFAULT_SLOT_COUNTS),
    }


def recommend_from_board(
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
    data = load_rankings()
    return recommend(
        list(data.get("players") or []),
        league_size=league_size,
        scoring=scoring,
        user_slot=user_slot,
        picks=picks,
        roster_template=roster_template,
        roster_size=roster_size,
        slot_counts=slot_counts,
        position_maxes=position_maxes,
        superflex=superflex,
        debug=debug,
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
    slot_counts: dict[str, Any] | None = None,
    position_maxes: dict[str, Any] | None = None,
    superflex: bool = False,
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
        slot_counts=slot_counts,
        position_maxes=position_maxes,
        superflex=superflex,
    )


def apply_pick_from_board(
    *,
    player_id: str,
    league_size: int,
    scoring: str,
    picks: list[dict[str, Any]],
    roster_template: list[str] | None = None,
    roster_size: int | None = None,
    slot_counts: dict[str, Any] | None = None,
    position_maxes: dict[str, Any] | None = None,
    superflex: bool = False,
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
    data = load_rankings()
    return apply_pick(
        player_id=player_id,
        picks=picks,
        players=list(data.get("players") or []),
        settings=settings,
    )


# Re-exports for engine consumers / tests
__all__ = [
    "DEFAULT_POSITION_MAXES",
    "DEFAULT_ROSTER_SIZE",
    "DEFAULT_ROSTER_TEMPLATE",
    "FLEX_ELIGIBLE",
    "POSITION_KEYS",
    "LeagueSettings",
    "apply_pick_from_board",
    "available_players",
    "can_add_position",
    "can_team_draft_player",
    "compute_open_needs",
    "count_by_position",
    "drafted_ids",
    "evaluate_player",
    "evaluate_player_from_board",
    "fit_pct_from_scores",
    "get_eligible_players",
    "league_settings_from_request",
    "list_players_for_api",
    "load_rankings",
    "next_overall_after",
    "normalize_position_maxes",
    "normalize_scoring",
    "player_fills_need",
    "power_rank_from_consensus",
    "rank_for_scoring",
    "recommend",
    "recommend_from_board",
    "resolve_roster_template",
    "round_for_overall",
    "snake_draft_order",
    "team_roster_from_picks",
    "team_slot_for_overall",
    "undo_last_pick",
    "validate_pick",
]
