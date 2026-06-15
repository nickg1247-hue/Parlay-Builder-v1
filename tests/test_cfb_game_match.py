"""CFB game crosswalk tests — ESPN / CFBD / Odds API team + date matching."""

from datetime import date

import pandas as pd

from app.odds.cfb_game_match import (
    attach_cfbd_ids_to_slate,
    build_cfbd_lines_index,
    match_key,
    resolve_cfbd_game_id,
)


def test_match_key_pitt_alias():
    key = match_key("2024-11-30", "Pittsburgh Panthers", "West Virginia")
    assert key == ("2024-11-30", "Pitt", "West Virginia")


def test_match_key_ole_miss_alias():
    key = match_key(date(2024, 9, 7), "Mississippi Rebels", "Georgia")
    assert key == ("2024-09-07", "Ole Miss", "Georgia")


def test_match_key_nc_state_alias():
    key = match_key("2024-11-30", "North Carolina State Wolfpack", "North Carolina")
    assert key == ("2024-11-30", "NC State", "North Carolina")


def test_resolve_cfbd_game_id_by_team_date():
    cfbd_rows = [
        {
            "cfbd_game_id": "401628472",
            "game_date": "2024-11-30",
            "home_team": "Pitt",
            "away_team": "West Virginia",
            "ou_line": 52.5,
        }
    ]
    index = build_cfbd_lines_index(cfbd_rows)
    slate_row = {
        "game_id": "401635999",
        "date": "2024-11-30",
        "home_team": "Pittsburgh",
        "away_team": "West Virginia",
    }
    assert resolve_cfbd_game_id(slate_row, index) == "401628472"


def test_attach_cfbd_ids_to_slate():
    cfbd_rows = [
        {
            "cfbd_game_id": "100",
            "game_date": "2024-11-30",
            "home_team": "Ole Miss",
            "away_team": "Mississippi State",
        }
    ]
    index = build_cfbd_lines_index(cfbd_rows)
    slate = pd.DataFrame(
        [
            {
                "game_id": "espn-1",
                "date": "2024-11-30",
                "home_team": "Ole Miss",
                "away_team": "Mississippi State",
            },
            {
                "game_id": "espn-2",
                "date": "2024-11-30",
                "home_team": "Alabama",
                "away_team": "Auburn",
            },
        ]
    )
    enriched = attach_cfbd_ids_to_slate(slate, index)
    assert enriched.loc[0, "cfbd_game_id"] == "100"
    assert pd.isna(enriched.loc[1, "cfbd_game_id"])
