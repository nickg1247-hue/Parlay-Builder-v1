"""HARD eligibility gate — single source of truth for legal drafts."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.settings import (
    POSITION_KEYS,
    LeagueSettings,
    normalize_position_maxes,
)


def count_by_position(rostered: list[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in POSITION_KEYS}
    for p in rostered:
        pos = str(p.get("position") or "")
        if pos in counts:
            counts[pos] += 1
    return counts


def team_roster_from_picks(
    picks: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
    team_slot: int,
    *,
    include_unknown: bool = True,
) -> list[dict[str, Any]]:
    """
    Build a team's rostered player dicts.

    If include_unknown and a pick's player_id is missing from the board,
    synthesize a stub from pick metadata when present so position maxes
    cannot be undercounted.
    """
    roster: list[dict[str, Any]] = []
    for p in picks:
        if int(p.get("team_slot") or 0) != int(team_slot):
            continue
        pid = str(p.get("player_id") or "")
        if pid in players_by_id:
            roster.append(players_by_id[pid])
            continue
        if include_unknown:
            pos = str(p.get("position") or "")
            roster.append(
                {
                    "player_id": pid or f"unknown_{len(roster)}",
                    "name": p.get("name") or pid or "Unknown",
                    "position": pos or "RB",
                    "_unknown": True,
                }
            )
    return roster


def can_team_draft_player(
    team_roster: list[dict[str, Any]],
    player: dict[str, Any],
    settings: LeagueSettings | dict[str, Any],
) -> bool:
    """
    True iff the player can legally be drafted onto this roster.

    Checks (in order):
    1. Position maximum
    2. Total roster capacity (starters + bench; IR excluded from draft capacity)
    3. Player already on this roster (caller usually filters via available pool)

    A player may be drafted to the bench even when all starting slots for
    their position (and WRT) are already filled — that is intentional.
    """
    if isinstance(settings, LeagueSettings):
        maxes = settings.position_maxes
        capacity = settings.roster_capacity
    else:
        maxes = normalize_position_maxes(settings.get("position_maxes"))
        capacity = int(
            settings.get("roster_capacity")
            or settings.get("roster_size")
            or settings.get("rounds")
            or 99
        )

    pos = str(player.get("position") or "")
    if pos in maxes and count_by_position(team_roster).get(pos, 0) >= int(maxes[pos]):
        return False
    if len(team_roster) >= capacity:
        return False
    return True


def get_eligible_players(
    team_roster: list[dict[str, Any]],
    available_players: list[dict[str, Any]],
    settings: LeagueSettings,
) -> list[dict[str, Any]]:
    """Players this team is legally allowed to draft right now."""
    return [
        p
        for p in available_players
        if can_team_draft_player(team_roster, p, settings)
    ]


def drafted_ids(picks: list[dict[str, Any]]) -> set[str]:
    return {str(p["player_id"]) for p in picks if p.get("player_id") is not None}


def available_pool(
    players: list[dict[str, Any]], picks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    taken = drafted_ids(picks)
    return [p for p in players if str(p.get("player_id")) not in taken]


def validate_pick(
    *,
    player_id: str,
    team_slot: int,
    overall: int,
    picks: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
    settings: LeagueSettings,
) -> dict[str, Any]:
    """
    Authoritative pre-commit validation.
    Returns {ok: True} or {ok: False, error, detail}.
    """
    pid = str(player_id)
    if pid in drafted_ids(picks):
        return {
            "ok": False,
            "error": "duplicate_player",
            "detail": f"Player {pid} is already drafted",
        }
    player = players_by_id.get(pid)
    if player is None:
        return {
            "ok": False,
            "error": "unknown_player",
            "detail": f"Unknown player_id {pid}",
        }
    roster = team_roster_from_picks(picks, players_by_id, team_slot)
    if len(roster) >= settings.roster_capacity:
        return {
            "ok": False,
            "error": "roster_full",
            "detail": (
                f"Team {team_slot} already has {len(roster)}/"
                f"{settings.roster_capacity} players (starters + bench)"
            ),
        }
    if not can_team_draft_player(roster, player, settings):
        counts = count_by_position(roster)
        pos = str(player.get("position") or "")
        if counts.get(pos, 0) >= int(settings.position_maxes.get(pos, 99)):
            return {
                "ok": False,
                "error": "position_max",
                "detail": (
                    f"Team {team_slot} already has {counts.get(pos, 0)} {pos} "
                    f"(max {settings.position_maxes.get(pos)})"
                ),
                "team_slot": team_slot,
                "overall": overall,
                "position": pos,
                "counts": counts,
                "maxes": dict(settings.position_maxes),
            }
        return {
            "ok": False,
            "error": "roster_full",
            "detail": f"Team {team_slot} cannot draft more players",
        }
    return {"ok": True, "player": player}
