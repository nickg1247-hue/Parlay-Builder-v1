"""ADP / next-pick availability estimates."""

from __future__ import annotations

from typing import Any

from app.services.fantasy_draft.eligibility import (
    can_team_draft_player,
    count_by_position,
    team_roster_from_picks,
)
from app.services.fantasy_draft.projections import rank_for_scoring
from app.services.fantasy_draft.roster import compute_open_needs
from app.services.fantasy_draft.settings import LeagueSettings


def team_slot_for_overall(overall: int, league_size: int) -> int:
    round_num = (overall - 1) // league_size + 1
    pick_in_round = (overall - 1) % league_size + 1
    if round_num % 2 == 1:
        return pick_in_round
    return league_size - pick_in_round + 1


def next_overall_for_team(
    league_size: int, team_slot: int, current_overall: int, total_picks: int
) -> int | None:
    for o in range(current_overall, total_picks + 1):
        if team_slot_for_overall(o, league_size) == team_slot:
            return o
    return None


def probability_available_next_pick(
    player: dict[str, Any],
    *,
    settings: LeagueSettings,
    current_overall: int,
    team_next_overall: int,
    picks: list[dict[str, Any]],
    players_by_id: dict[str, dict[str, Any]],
) -> float:
    """
    Estimate P(player still available at team's next pick).
    Uses ADP distance + intervening teams' roster needs.
    """
    picks_until = max(0, team_next_overall - current_overall)
    if picks_until <= 0:
        return 0.0

    adp = float(player.get("adp") or rank_for_scoring(player, settings.scoring))
    # Baseline: logistic-ish from ADP vs current pick
    # If ADP >> current pick, likely survives; if ADP << current, unlikely
    reach = adp - current_overall
    # Base survival over `picks_until` picks
    # Each intervening pick has chance to take this player
    base_take = 0.08
    if reach < -8:
        base_take = 0.22
    elif reach < 0:
        base_take = 0.14
    elif reach < 12:
        base_take = 0.07
    else:
        base_take = 0.035

    pos = str(player.get("position") or "")
    # Boost take-probability when intervening teams need this position
    need_boost = 0.0
    for o in range(current_overall, team_next_overall):
        slot = team_slot_for_overall(o, settings.league_size)
        roster = team_roster_from_picks(picks, players_by_id, slot)
        if not can_team_draft_player(roster, player, settings):
            continue
        needs = compute_open_needs(roster, settings)
        if pos in needs or (
            "FLEX" in needs and pos in {"RB", "WR", "TE"}
        ) or (
            any(s in ("SUPERFLEX", "SF") for s in needs) and pos == "QB"
        ):
            need_boost += 0.045
        else:
            need_boost += 0.01

    p_taken = min(0.92, picks_until * base_take + need_boost)
    return max(0.02, min(0.98, 1.0 - p_taken))


def adp_value(
    player: dict[str, Any],
    *,
    current_overall: int,
    scoring: str,
) -> float:
    """Positive = value (falling), negative = reach. Normalized ~ -1..1."""
    adp = float(player.get("adp") or rank_for_scoring(player, scoring))
    delta = adp - current_overall  # + means available later than ADP (value)
    return max(-1.0, min(1.0, delta / 24.0))
