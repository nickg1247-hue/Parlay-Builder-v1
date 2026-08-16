"""CFP field selection rules and title-game detection."""

from app.services.cfb_futures import mark_title_games
from app.services.cfb_playoff import (
    ACTUAL_FIELDS,
    champ_auto_score,
    field_hit_rate,
    playoff_rules_year,
    select_playoff_indices,
)


def test_rules_change_after_2025():
    assert playoff_rules_year(2024) == "champs5_bye_champs"
    assert playoff_rules_year(2025) == "champs5_bye_top4"
    assert playoff_rules_year(2026) == "p4_plus_g6_bye_top4"


def test_eight_five_p4_champ_loses_auto_to_g5():
    """Duke 8-5 should not take a 2025 auto bid from Tulane 11-2."""
    duke = champ_auto_score(wins=8, strength=1620, conf_key="acc")
    tulane = champ_auto_score(wins=11, strength=1560, conf_key="aac")
    jmu = champ_auto_score(wins=12, strength=1540, conf_key="sun_belt")
    clemson = champ_auto_score(wins=10, strength=1680, conf_key="acc")
    assert tulane > duke
    assert jmu > duke
    assert clemson > tulane


def test_2026_rules_keep_all_p4_champs():
    teams = [f"T{i}" for i in range(16)]
    team_conf = {teams[i]: conf for i, conf in enumerate(
        ["sec", "big_ten", "big_12", "acc"] + ["aac"] * 4 + ["mwc"] * 8
    )}
    champs = {"sec": 0, "big_ten": 1, "big_12": 2, "acc": 3, "aac": 4}
    wins = [8.0, 8.0, 8.0, 8.0] + [12.0] * 12
    strength = [1550.0] * 4 + [1700.0] * 12
    field, auto = select_playoff_indices(
        champs=champs,
        wins=wins,
        strength=strength,
        teams=teams,
        team_conf=team_conf,
        season=2026,
        season_progress=1.0,
    )
    assert {0, 1, 2, 3}.issubset(auto)
    assert len(field) == 12


def test_week_15_same_conference_is_title_game():
    games = mark_title_games(
        [
            {
                "week": 15,
                "home_team": "Virginia",
                "away_team": "Duke",
                "home_conference": "ACC",
                "away_conference": "ACC",
                "conference_game": 0,
                "home_win": 0,
            }
        ]
    )
    assert games[0]["title_game"] is True


def test_actual_fields_are_twelve():
    assert len(ACTUAL_FIELDS[2024]) == 12
    assert len(ACTUAL_FIELDS[2025]) == 12
    assert field_hit_rate(list(ACTUAL_FIELDS[2024]), ACTUAL_FIELDS[2024]) == 1.0
