"""Fantasy draft engine package — eligibility-first, ERVA-style recommendations."""

from app.services.fantasy_draft.engine import (
    advance_mock_draft,
    apply_pick,
    cpu_select_player,
    recommend_for_team,
    simulate_full_draft,
)
from app.services.fantasy_draft.eligibility import (
    can_team_draft_player,
    get_eligible_players,
    validate_pick,
)
from app.services.fantasy_draft.settings import (
    DEFAULT_DRAFT_WEIGHTS,
    DEFAULT_POSITION_MAXES,
    DEFAULT_ROSTER_SIZE,
    DEFAULT_SLOT_COUNTS,
    DEFAULT_STARTER_TEMPLATE,
    LeagueSettings,
)
from app.services.fantasy_draft.roster import optimize_starting_lineup

__all__ = [
    "DEFAULT_DRAFT_WEIGHTS",
    "DEFAULT_POSITION_MAXES",
    "DEFAULT_ROSTER_SIZE",
    "DEFAULT_SLOT_COUNTS",
    "DEFAULT_STARTER_TEMPLATE",
    "LeagueSettings",
    "advance_mock_draft",
    "apply_pick",
    "can_team_draft_player",
    "cpu_select_player",
    "get_eligible_players",
    "optimize_starting_lineup",
    "recommend_for_team",
    "simulate_full_draft",
    "validate_pick",
]
