"""ERVA draft engine — eligibility, VORP, stress sims."""

from __future__ import annotations

import pytest

from app.services.fantasy_draft.eligibility import (
    can_team_draft_player,
    get_eligible_players,
    validate_pick,
)
from app.services.fantasy_draft.engine import (
    apply_pick,
    cpu_select_player,
    recommend_for_team,
    simulate_full_draft,
)
from app.services.fantasy_draft.settings import league_settings_from_request
from app.services.nfl_fantasy_draft import load_rankings, recommend


def _mini_board():
    """Compact board for unit tests."""
    players = []
    # 6 QB, 12 RB, 12 WR, 6 TE, 4 DST, 4 K
    specs = [
        ("QB", 6, 300),
        ("RB", 12, 280),
        ("WR", 12, 270),
        ("TE", 6, 200),
        ("DST", 4, 120),
        ("K", 4, 130),
    ]
    n = 0
    for pos, count, top in specs:
        for i in range(count):
            n += 1
            players.append(
                {
                    "player_id": f"{pos.lower()}_{i}",
                    "name": f"{pos} {i}",
                    "position": pos,
                    "team": "XX",
                    "bye": 5 + (i % 10),
                    "adp": float(n),
                    "rank_std": n,
                    "rank_half": n,
                    "rank_ppr": n,
                    "tier": 1 + i // 3,
                    "proj_pts_half": top - i * 8,
                    "proj_pts_ppr": top - i * 8 + 5,
                    "proj_pts_std": top - i * 8 - 5,
                }
            )
    return players


def test_rb_max_excludes_all_rbs_from_eligible():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=10,
        position_maxes={"QB": 2, "RB": 4, "WR": 6, "TE": 2, "DST": 1, "K": 1},
        roster_size=15,
    )
    roster = [p for p in players if p["position"] == "RB"][:4]
    eligible = get_eligible_players(roster, players, settings)
    assert all(p["position"] != "RB" for p in eligible)
    assert can_team_draft_player(roster, players[6], settings) is False  # an RB


def test_qb_max_blocks_cpu_and_apply():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=8,
        position_maxes={"QB": 2, "RB": 5, "WR": 5, "TE": 2, "DST": 1, "K": 1},
        roster_size=12,
    )
    picks = []
    # Force team 1 to already have 2 QBs via apply_pick
    qbs = [p for p in players if p["position"] == "QB"]
    for i, qb in enumerate(qbs[:2]):
        # Manually place on team 1 with correct overall for slot 1 snake: 1, 16, ...
        overall = 1 if i == 0 else 16
        picks.append(
            {"overall": overall, "team_slot": 1, "player_id": qb["player_id"]}
        )
    # Try third QB
    bad = apply_pick(
        player_id=qbs[2]["player_id"],
        picks=picks,
        players=players,
        settings=settings,
        overall=17,
        team_slot=1,
    )
    assert bad["ok"] is False
    assert bad["error"]["error"] == "position_max"


def test_recommend_contains_zero_rbs_at_max():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=10,
        position_maxes={"QB": 4, "RB": 2, "WR": 8, "TE": 3, "DST": 3, "K": 3},
        roster_size=15,
    )
    rbs = [p for p in players if p["position"] == "RB"]
    picks = [
        {"overall": 1, "team_slot": 1, "player_id": rbs[0]["player_id"]},
        {"overall": 20, "team_slot": 1, "player_id": rbs[1]["player_id"]},
    ]
    # Pad other overalls so next for team 1 is upcoming — recommend still uses team roster
    rec = recommend_for_team(
        players, settings=settings, team_slot=1, picks=picks, run_lookahead=False
    )
    pool = [rec["primary"]] + list(rec.get("alternates") or [])
    pool = [p for p in pool if p]
    assert all(p["position"] != "RB" for p in pool)
    assert all(p["position"] != "RB" for p in (rec.get("top_pool") or []))


def test_duplicate_player_rejected():
    players = _mini_board()
    settings = league_settings_from_request(league_size=10, roster_size=15)
    pid = players[0]["player_id"]
    picks = [{"overall": 1, "team_slot": 1, "player_id": pid}]
    by_id = {str(p["player_id"]): p for p in players}
    v = validate_pick(
        player_id=pid,
        team_slot=2,
        overall=2,
        picks=picks,
        players_by_id=by_id,
        settings=settings,
    )
    assert v["ok"] is False
    assert v["error"] == "duplicate_player"


def test_wrt_slots_open_when_dedicated_filled():
    from app.services.fantasy_draft.roster import compute_open_needs, optimize_starting_lineup

    settings = league_settings_from_request(
        league_size=10,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    roster = []
    for pos in ["QB", "RB", "RB", "WR", "WR", "TE", "DST", "K"]:
        roster.append(
            {
                "position": pos,
                "player_id": f"{pos}_{len(roster)}",
                "proj_pts_half": 200 - len(roster),
            }
        )
    needs = compute_open_needs(roster, settings)
    assert needs == ["WRT", "WRT"]


def test_optimize_lineup_puts_extra_rb_in_wrt():
    from app.services.fantasy_draft.roster import optimize_starting_lineup

    settings = league_settings_from_request(
        league_size=12,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    roster = [
        {"player_id": "rb1", "name": "RB1", "position": "RB", "proj_pts_half": 280},
        {"player_id": "rb2", "name": "RB2", "position": "RB", "proj_pts_half": 260},
        {"player_id": "rb3", "name": "RB3", "position": "RB", "proj_pts_half": 240},
        {"player_id": "rb4", "name": "RB4", "position": "RB", "proj_pts_half": 200},
        {"player_id": "wr1", "name": "WR1", "position": "WR", "proj_pts_half": 270},
        {"player_id": "wr2", "name": "WR2", "position": "WR", "proj_pts_half": 250},
        {"player_id": "wr3", "name": "WR3", "position": "WR", "proj_pts_half": 235},
        {"player_id": "te1", "name": "TE1", "position": "TE", "proj_pts_half": 180},
    ]
    opt = optimize_starting_lineup(roster, settings)
    starter_ids = {
        r["player"]["player_id"] for r in opt["starters"] if r["player"]
    }
    assert "rb1" in starter_ids and "rb2" in starter_ids
    assert "wr1" in starter_ids and "wr2" in starter_ids
    assert "te1" in starter_ids
    wrt_ids = [
        r["player"]["player_id"]
        for r in opt["starters"]
        if r["slot"] == "WRT" and r["player"]
    ]
    # Best remaining skill players by proj: rb3 (240) + wr3 (235) beat rb4 (200)
    assert set(wrt_ids) == {"rb3", "wr3"}
    bench_ids = {p["player_id"] for p in opt["bench"]}
    assert "rb4" in bench_ids
    assert opt["counts"]["wrt_filled"] == 2
    assert opt["counts"]["bench_filled"] == 1


def test_bench_capacity_hard_block():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=8,
        slot_counts={
            "QB": 1,
            "RB": 1,
            "WR": 1,
            "TE": 0,
            "WRT": 0,
            "K": 0,
            "DST": 0,
            "BENCH": 1,
            "SUPERFLEX": 0,
            "IR": 0,
        },
        position_maxes={"QB": 4, "RB": 8, "WR": 8, "TE": 3, "DST": 3, "K": 3},
    )
    assert settings.roster_capacity == 4
    roster = players[:4]
    assert can_team_draft_player(roster, players[5], settings) is False


def test_recommend_includes_projected_role():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=10,
        scoring="half_ppr",
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    rec = recommend_for_team(
        players, settings=settings, team_slot=1, picks=[], run_lookahead=False
    )
    assert rec["primary"] is not None
    assert "projected_role" in rec["primary"]
    assert rec["primary"]["projected_role"]["label"]
    assert "lineup" in rec["board_meta"]


def test_mock_advance_until_user_stops_on_user_slot():
    from app.services.fantasy_draft.engine import advance_mock_draft
    from app.services.fantasy_draft.availability import team_slot_for_overall

    players = _mini_board()
    # Expand board so 8-team draft has enough players
    extra = []
    for i in range(40):
        extra.append(
            {
                "player_id": f"extra_{i}",
                "name": f"Extra {i}",
                "position": ["RB", "WR", "QB", "TE"][i % 4],
                "team": "XX",
                "bye": 7,
                "adp": 50 + i,
                "rank_std": 50 + i,
                "rank_half": 50 + i,
                "rank_ppr": 50 + i,
                "tier": 3,
                "proj_pts_half": 150 - i,
            }
        )
    players = players + extra
    settings = league_settings_from_request(
        league_size=8,
        slot_counts={
            "QB": 1,
            "RB": 1,
            "WR": 1,
            "TE": 0,
            "WRT": 0,
            "K": 0,
            "DST": 0,
            "BENCH": 1,
        },
        position_maxes={"QB": 3, "RB": 5, "WR": 5, "TE": 2, "DST": 1, "K": 1},
    )
    user_slot = 3
    result = advance_mock_draft(
        players,
        settings=settings,
        picks=[],
        user_slot=user_slot,
        mode="until_user",
        seed=42,
    )
    assert result["ok"] is True
    assert result["on_clock_slot"] == user_slot
    assert len(result["cpu_picks"]) == user_slot - 1
    # Next overall belongs to user
    nxt = max(p["overall"] for p in result["picks"]) + 1 if result["picks"] else 1
    assert team_slot_for_overall(nxt, settings.league_size) == user_slot


def test_mock_advance_finish_completes_draft():
    from app.services.fantasy_draft.engine import advance_mock_draft

    data = load_rankings()
    players = list(data["players"])
    settings = league_settings_from_request(
        league_size=8,
        scoring="half_ppr",
        slot_counts={
            "QB": 1,
            "RB": 1,
            "WR": 1,
            "TE": 0,
            "WRT": 0,
            "K": 0,
            "DST": 0,
            "BENCH": 1,
        },
        position_maxes={"QB": 3, "RB": 5, "WR": 5, "TE": 2, "DST": 1, "K": 1},
    )
    result = advance_mock_draft(
        players,
        settings=settings,
        picks=[],
        user_slot=1,
        mode="finish",
        seed=3,
    )
    assert result["ok"] is True
    assert result["done"] is True
    assert len(result["picks"]) == settings.total_picks


def test_flex_lineup_impact_credits_wrt_upgrade():
    from app.services.fantasy_draft.roster import flex_lineup_impact, roster_need_score

    settings = league_settings_from_request(
        league_size=12,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    roster = [
        {"player_id": "qb1", "position": "QB", "proj_pts_half": 300},
        {"player_id": "rb1", "position": "RB", "proj_pts_half": 280},
        {"player_id": "rb2", "position": "RB", "proj_pts_half": 250},
        {"player_id": "wr1", "position": "WR", "proj_pts_half": 270},
        {"player_id": "wr2", "position": "WR", "proj_pts_half": 240},
        {"player_id": "te1", "position": "TE", "proj_pts_half": 180},
        {"player_id": "wrt_weak", "position": "WR", "proj_pts_half": 160},
        {"player_id": "wrt2", "position": "RB", "proj_pts_half": 170},
        {"player_id": "k1", "position": "K", "proj_pts_half": 120},
        {"player_id": "d1", "position": "DST", "proj_pts_half": 110},
    ]
    # Dedicated + both WRT filled with weak WR at floor ~160
    cand = {
        "player_id": "stud_rb",
        "position": "RB",
        "proj_pts_half": 230,
        "name": "Stud RB",
    }
    flex = flex_lineup_impact(roster, cand, settings)
    assert flex["eligible"] is True
    assert flex["would_start_wrt"] is True
    assert flex["delta"] >= 50  # 230 vs ~160 floor

    need = roster_need_score(
        cand,
        open_needs=[],
        settings=settings,
        draft_progress=0.5,
        rostered=roster,
    )
    # No open starters, but strong WRT upgrade should still register some need
    assert need >= 0.2

    open_need = roster_need_score(
        {"player_id": "x", "position": "WR", "proj_pts_half": 200},
        open_needs=["WRT", "WRT"],
        settings=settings,
        draft_progress=0.4,
        rostered=roster[:6],  # WRT empty
    )
    assert open_need >= 0.55


def test_wrt_scarcity_higher_than_single_flex():
    from app.services.fantasy_draft.scarcity import positional_scarcity

    players = _mini_board()
    one = league_settings_from_request(
        league_size=10,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 1,
            "K": 1,
            "DST": 1,
            "BENCH": 5,
        },
    )
    two = league_settings_from_request(
        league_size=10,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    s1 = positional_scarcity(players, one)
    s2 = positional_scarcity(players, two)
    # Extra WRT should not make RB/WR less scarce
    assert s2["RB"] >= s1["RB"] - 0.01
    assert s2["WR"] >= s1["WR"] - 0.01


def test_needs_update_after_pick_changes_recommendation_context():
    """Open needs shrink after filling a slot; need scores shift with roster."""
    from app.services.fantasy_draft.roster import compute_open_needs, roster_need_score

    players = _mini_board()
    settings = league_settings_from_request(
        league_size=10,
        slot_counts={
            "QB": 1,
            "RB": 2,
            "WR": 2,
            "TE": 1,
            "WRT": 2,
            "K": 1,
            "DST": 1,
            "BENCH": 6,
        },
    )
    empty_needs = compute_open_needs([], settings)
    assert "QB" in empty_needs
    assert empty_needs.count("WRT") == 2

    qb = next(p for p in players if p["position"] == "QB")
    after_qb = compute_open_needs([qb], settings)
    assert "QB" not in after_qb
    assert len(after_qb) == len(empty_needs) - 1

    need_before = roster_need_score(
        qb,
        empty_needs,
        settings,
        draft_progress=0.2,
        rostered=[],
        available=players,
        remaining_team_picks=14,
    )
    wr = next(p for p in players if p["position"] == "WR")
    need_wr_while_qb_open = roster_need_score(
        wr,
        empty_needs,
        settings,
        draft_progress=0.2,
        rostered=[],
        available=players,
        remaining_team_picks=14,
    )
    # Filling open QB should score as higher need than a WR when QB is empty
    # (WR can still fill WRT, so not necessarily tiny — but QB dedicated wins)
    assert need_before > need_wr_while_qb_open

    # After QB filled, another QB is low need; WR rises relatively for WRT/WR
    need_qb_after = roster_need_score(
        next(p for p in players if p["position"] == "QB" and p["player_id"] != qb["player_id"]),
        after_qb,
        settings,
        draft_progress=0.25,
        rostered=[qb],
        available=players,
        remaining_team_picks=13,
    )
    need_wr_after = roster_need_score(
        wr,
        after_qb,
        settings,
        draft_progress=0.25,
        rostered=[qb],
        available=players,
        remaining_team_picks=13,
    )
    assert need_wr_after > need_qb_after


def test_position_max_overrides_flex():
    players = _mini_board()
    settings = league_settings_from_request(
        league_size=10,
        position_maxes={"QB": 4, "RB": 2, "WR": 8, "TE": 3, "DST": 3, "K": 3},
        roster_size=15,
    )
    roster = [p for p in players if p["position"] == "RB"][:2]
    # Even if WRT open, RB at max is illegal
    assert get_eligible_players(roster, players, settings)
    assert all(p["position"] != "RB" for p in get_eligible_players(roster, players, settings))


def test_recommendations_are_team_specific():
    data = load_rankings()
    players = list(data["players"])
    settings = league_settings_from_request(
        league_size=10, scoring="half_ppr", roster_size=15
    )
    # Team 1 has elite RBs
    rbs = [p for p in players if p["position"] == "RB"][:2]
    picks = [
        {"overall": 1, "team_slot": 1, "player_id": rbs[0]["player_id"]},
        {"overall": 20, "team_slot": 1, "player_id": rbs[1]["player_id"]},
    ]
    # Team 2 empty
    a = recommend_for_team(
        players, settings=settings, team_slot=1, picks=picks, run_lookahead=False
    )
    b = recommend_for_team(
        players, settings=settings, team_slot=2, picks=picks, run_lookahead=False
    )
    assert a["primary"] and b["primary"]
    # Different rosters → often different top pick (not guaranteed, but scores differ)
    assert a["primary"]["player_id"] != b["primary"]["player_id"] or a["primary"]["erva"] != b["primary"].get("erva")


def test_stress_simulated_draft_zero_violations():
    data = load_rankings()
    players = list(data["players"])
    settings = league_settings_from_request(
        league_size=8,
        scoring="half_ppr",
        roster_size=12,
        position_maxes={"QB": 2, "RB": 5, "WR": 5, "TE": 2, "DST": 1, "K": 1},
    )
    result = simulate_full_draft(players, settings=settings, seed=7)
    assert result["ok"] is True, result["violations"]
    assert result["picks_made"] == settings.total_picks
    assert result["violations"] == []


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_multiple_mock_drafts_legal(seed):
    data = load_rankings()
    players = list(data["players"])
    settings = league_settings_from_request(
        league_size=10,
        scoring="ppr",
        roster_size=15,
        position_maxes={"QB": 3, "RB": 6, "WR": 6, "TE": 3, "DST": 2, "K": 2},
    )
    result = simulate_full_draft(players, settings=settings, seed=seed)
    assert result["ok"], result["violations"]


def test_legacy_recommend_api_still_works():
    data = load_rankings()
    rec = recommend(
        list(data["players"]),
        league_size=10,
        scoring="half_ppr",
        user_slot=3,
        picks=[],
        roster_size=15,
    )
    assert rec["primary"] is not None
    assert "reasons" in rec["primary"]
    assert rec["board_meta"]["position_maxes"]["RB"] >= 1
