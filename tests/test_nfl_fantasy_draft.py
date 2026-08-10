"""Pure-function tests for NFL fantasy snake draft helper."""

from __future__ import annotations

import pytest

from app.services import nfl_fantasy_draft as draft


SAMPLE_PLAYERS = [
    {
        "player_id": "rb_a",
        "name": "Alpha RB",
        "position": "RB",
        "team": "KC",
        "bye": 10,
        "adp": 1.0,
        "rank_std": 1,
        "rank_half": 1,
        "rank_ppr": 2,
        "tier": 1,
    },
    {
        "player_id": "wr_a",
        "name": "Alpha WR",
        "position": "WR",
        "team": "BUF",
        "bye": 7,
        "adp": 2.0,
        "rank_std": 2,
        "rank_half": 2,
        "rank_ppr": 1,
        "tier": 1,
    },
    {
        "player_id": "rb_b",
        "name": "Beta RB",
        "position": "RB",
        "team": "SF",
        "bye": 14,
        "adp": 5.0,
        "rank_std": 5,
        "rank_half": 5,
        "rank_ppr": 6,
        "tier": 2,
    },
    {
        "player_id": "qb_a",
        "name": "Alpha QB",
        "position": "QB",
        "team": "BAL",
        "bye": 14,
        "adp": 20.0,
        "rank_std": 20,
        "rank_half": 20,
        "rank_ppr": 20,
        "tier": 4,
    },
    {
        "player_id": "wr_b",
        "name": "Beta WR",
        "position": "WR",
        "team": "MIA",
        "bye": 10,
        "adp": 8.0,
        "rank_std": 8,
        "rank_half": 8,
        "rank_ppr": 7,
        "tier": 2,
    },
    {
        "player_id": "te_a",
        "name": "Alpha TE",
        "position": "TE",
        "team": "KC",
        "bye": 10,
        "adp": 25.0,
        "rank_std": 25,
        "rank_half": 25,
        "rank_ppr": 22,
        "tier": 3,
    },
]


def test_snake_order_odd_even_rounds():
    order = draft.snake_draft_order(10, 2)
    assert len(order) == 20
    assert order[0] == {"overall": 1, "round": 1, "team_slot": 1}
    assert order[9]["team_slot"] == 10
    # Round 2 snakes: overall 11 → slot 10, overall 12 → slot 9
    assert draft.team_slot_for_overall(11, 10) == 10
    assert draft.team_slot_for_overall(12, 10) == 9
    assert draft.team_slot_for_overall(20, 10) == 1


def test_snake_order_slot_3_in_10():
    # Slot 3 picks overall 3, then 18, then 23, ...
    assert draft.team_slot_for_overall(3, 10) == 3
    assert draft.team_slot_for_overall(18, 10) == 3
    assert draft.team_slot_for_overall(23, 10) == 3


def test_remove_drafted_from_available():
    picks = [{"overall": 1, "team_slot": 1, "player_id": "rb_a"}]
    avail = draft.available_players(SAMPLE_PLAYERS, picks)
    ids = {p["player_id"] for p in avail}
    assert "rb_a" not in ids
    assert "wr_a" in ids
    assert len(avail) == len(SAMPLE_PLAYERS) - 1


def test_undo_last_pick_pure():
    picks = [
        {"overall": 1, "team_slot": 1, "player_id": "rb_a"},
        {"overall": 2, "team_slot": 2, "player_id": "wr_a"},
        {"overall": 3, "team_slot": 3, "player_id": "rb_b"},
    ]
    undone = draft.undo_last_pick(picks)
    assert [p["overall"] for p in undone] == [1, 2]
    assert picks[-1]["player_id"] == "rb_b"  # original unchanged
    assert draft.undo_last_pick([]) == []


def test_need_bonus_prefers_missing_rb():
    """With WR slots filled and an RB hole open, prefer a lower-ranked RB over BPA WR."""
    players = [
        {
            "player_id": "wr_bpa",
            "name": "BPA WR",
            "position": "WR",
            "team": "CIN",
            "bye": 12,
            "adp": 4.0,
            "rank_std": 4,
            "rank_half": 4,
            "rank_ppr": 4,
            "tier": 1,
        },
        {
            "player_id": "rb_need",
            "name": "Need RB",
            "position": "RB",
            "team": "ATL",
            "bye": 12,
            "adp": 14.0,
            "rank_std": 14,
            "rank_half": 14,
            "rank_ppr": 14,
            "tier": 3,
        },
        {
            "player_id": "wr_owned1",
            "name": "Owned WR1",
            "position": "WR",
            "team": "MIN",
            "bye": 6,
            "adp": 2.0,
            "rank_std": 2,
            "rank_half": 2,
            "rank_ppr": 2,
            "tier": 1,
        },
        {
            "player_id": "wr_owned2",
            "name": "Owned WR2",
            "position": "WR",
            "team": "DAL",
            "bye": 7,
            "adp": 6.0,
            "rank_std": 6,
            "rank_half": 6,
            "rank_ppr": 6,
            "tier": 2,
        },
        {
            "player_id": "te_owned",
            "name": "Owned TE",
            "position": "TE",
            "team": "KC",
            "bye": 10,
            "adp": 30.0,
            "rank_std": 30,
            "rank_half": 30,
            "rank_ppr": 28,
            "tier": 4,
        },
        {
            "player_id": "flex_owned",
            "name": "Owned Flex WR",
            "position": "WR",
            "team": "DET",
            "bye": 5,
            "adp": 18.0,
            "rank_std": 18,
            "rank_half": 18,
            "rank_ppr": 15,
            "tier": 3,
        },
        {
            "player_id": "qb_owned",
            "name": "Owned QB",
            "position": "QB",
            "team": "BAL",
            "bye": 14,
            "adp": 40.0,
            "rank_std": 40,
            "rank_half": 40,
            "rank_ppr": 40,
            "tier": 5,
        },
    ]
    # User (slot 1) has filled QB/WR/WR/TE/FLEX — still needs RB (x2), DST, K
    user_pids = [
        "qb_owned",
        "wr_owned1",
        "wr_owned2",
        "te_owned",
        "flex_owned",
    ]
    picks = []
    overall = 1
    for pid in user_pids:
        # Place on user slot 1's snake picks: 1, 20, 21, 40, 41 in 10-team
        while draft.team_slot_for_overall(overall, 10) != 1:
            pad_id = f"pad_{overall}"
            players.append(
                {
                    "player_id": pad_id,
                    "name": f"Pad {overall}",
                    "position": "DST",
                    "team": "XX",
                    "bye": 5,
                    "adp": 200 + overall,
                    "rank_std": 200 + overall,
                    "rank_half": 200 + overall,
                    "rank_ppr": 200 + overall,
                    "tier": 15,
                }
            )
            picks.append(
                {
                    "overall": overall,
                    "team_slot": draft.team_slot_for_overall(overall, 10),
                    "player_id": pad_id,
                }
            )
            overall += 1
        picks.append(
            {
                "overall": overall,
                "team_slot": 1,
                "player_id": pid,
            }
        )
        overall += 1

    # Advance to user's next pick on the clock
    while draft.team_slot_for_overall(overall, 10) != 1:
        pad_id = f"pad_{overall}"
        players.append(
            {
                "player_id": pad_id,
                "name": f"Pad {overall}",
                "position": "K",
                "team": "YY",
                "bye": 8,
                "adp": 210 + overall,
                "rank_std": 210 + overall,
                "rank_half": 210 + overall,
                "rank_ppr": 210 + overall,
                "tier": 16,
            }
        )
        picks.append(
            {
                "overall": overall,
                "team_slot": draft.team_slot_for_overall(overall, 10),
                "player_id": pad_id,
            }
        )
        overall += 1

    result = draft.recommend(
        players,
        league_size=10,
        scoring="half_ppr",
        user_slot=1,
        picks=picks,
    )
    assert result["board_meta"]["user_on_clock"] is True
    assert "RB" in result["board_meta"]["user_needs"]
    # Rank-4 WR is better BPA, but does not fill a need; RB should win via need_bonus
    assert result["primary"]["player_id"] == "rb_need"


def test_position_max_blocks_recommend():
    """With QB max 1 and a QB already rostered, do not recommend another QB."""
    players = [
        {
            "player_id": "qb_owned",
            "name": "Owned QB",
            "position": "QB",
            "team": "BAL",
            "bye": 14,
            "adp": 40,
            "rank_std": 40,
            "rank_half": 40,
            "rank_ppr": 40,
            "tier": 5,
        },
        {
            "player_id": "qb_elite",
            "name": "Elite QB",
            "position": "QB",
            "team": "BUF",
            "bye": 7,
            "adp": 15,
            "rank_std": 15,
            "rank_half": 15,
            "rank_ppr": 15,
            "tier": 2,
        },
        {
            "player_id": "rb_ok",
            "name": "Okay RB",
            "position": "RB",
            "team": "ATL",
            "bye": 12,
            "adp": 25,
            "rank_std": 25,
            "rank_half": 25,
            "rank_ppr": 25,
            "tier": 3,
        },
    ]
    picks = [{"overall": 1, "team_slot": 1, "player_id": "qb_owned"}]
    # Pad to user slot 1's next pick (overall 20 in 10-team)
    for o in range(2, 20):
        pid = f"pad_{o}"
        players.append(
            {
                "player_id": pid,
                "name": f"Pad {o}",
                "position": "WR",
                "team": "XX",
                "bye": 5,
                "adp": 100 + o,
                "rank_std": 100 + o,
                "rank_half": 100 + o,
                "rank_ppr": 100 + o,
                "tier": 12,
            }
        )
        picks.append(
            {
                "overall": o,
                "team_slot": draft.team_slot_for_overall(o, 10),
                "player_id": pid,
            }
        )

    assert draft.can_add_position(
        [{"position": "QB"}], "QB", {"QB": 1, "RB": 8, "WR": 8, "TE": 3, "DST": 3, "K": 3}
    ) is False

    result = draft.recommend(
        players,
        league_size=10,
        scoring="half_ppr",
        user_slot=1,
        picks=picks,
        roster_size=15,
        position_maxes={"QB": 1, "RB": 8, "WR": 8, "TE": 3, "DST": 3, "K": 3},
    )
    assert result["primary"] is not None
    assert result["primary"]["position"] != "QB"
    assert result["board_meta"]["position_maxes"]["QB"] == 1
    assert result["board_meta"]["user_position_counts"]["QB"] == 1


def test_normalize_position_maxes_clamps():
    out = draft.normalize_position_maxes({"QB": 99, "rb": 2})
    assert out["QB"] == 20
    assert out["RB"] == 2
    assert out["WR"] == draft.DEFAULT_POSITION_MAXES["WR"]


def test_scoring_column_selection():
    assert draft.rank_for_scoring(SAMPLE_PLAYERS[0], "half_ppr") == 1
    assert draft.rank_for_scoring(SAMPLE_PLAYERS[1], "ppr") == 1
    assert draft.rank_for_scoring(SAMPLE_PLAYERS[0], "standard") == 1


def test_evaluate_player_returns_projected_and_fit():
    result = draft.evaluate_player(
        SAMPLE_PLAYERS,
        player_id="rb_b",
        league_size=10,
        scoring="half_ppr",
        user_slot=3,
        picks=[],
    )
    assert result["player"]["projected_pick"] is not None
    assert result["player"]["power_rank"] >= 1
    assert result["fit_pct"] is not None
    assert result["drafted"] is False


def test_recommend_updates_after_pick():
    first = draft.recommend(
        SAMPLE_PLAYERS,
        league_size=10,
        scoring="half_ppr",
        user_slot=3,
        picks=[],
    )
    assert first["primary"]["player_id"] == "rb_a"
    picks = [
        {
            "overall": 1,
            "team_slot": 1,
            "player_id": "rb_a",
        }
    ]
    second = draft.recommend(
        SAMPLE_PLAYERS,
        league_size=10,
        scoring="half_ppr",
        user_slot=3,
        picks=picks,
    )
    assert second["primary"]["player_id"] != "rb_a"
    assert second["board_meta"]["user_next_overall"] == 3


def test_load_rankings_has_starter_board():
    data = draft.load_rankings()
    players = data["players"]
    assert 200 <= len(players) <= 280
    positions = {p["position"] for p in players}
    assert positions >= {"QB", "RB", "WR", "TE", "DST", "K"}


@pytest.mark.parametrize("size", [8, 10, 12, 14])
def test_default_roster_rounds(size):
    rounds = len(draft.DEFAULT_ROSTER_TEMPLATE)
    order = draft.snake_draft_order(size, rounds)
    assert len(order) == size * rounds
    # Each team appears exactly `rounds` times
    from collections import Counter

    counts = Counter(p["team_slot"] for p in order)
    assert all(counts[s] == rounds for s in range(1, size + 1))
