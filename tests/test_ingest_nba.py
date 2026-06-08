"""NBA historical ingest tests (mocked HTTP — no live network)."""

from datetime import date
from unittest.mock import patch

import pytest

from app.ingest import nba as ni
from app.db.nba_schema import NBA_GAMES_COLUMNS

HEADERS = [
    "TEAM_ID",
    "TEAM_ABBREVIATION",
    "TEAM_NAME",
    "GAME_ID",
    "GAME_DATE",
    "MATCHUP",
    "WL",
    "MIN",
    "PTS",
]


def _payload(rows: list[list]) -> dict:
    return {"resultSets": [{"name": "LeagueGameFinderResults", "headers": HEADERS, "rowSet": rows}]}


REGULAR_ROWS = [
    [1610612738, "BOS", "Boston Celtics", "0022300001", "2023-10-24T00:00:00", "BOS vs. NYK", "W", 240, 108],
    [1610612752, "NYK", "New York Knicks", "0022300001", "2023-10-24T00:00:00", "NYK @ BOS", "L", 240, 104],
    [1610612738, "BOS", "Boston Celtics", "0022300002", "2023-10-26T00:00:00", "BOS vs. MIA", "W", 240, 110],
    [1610612748, "MIA", "Miami Heat", "0022300002", "2023-10-26T00:00:00", "MIA @ BOS", "L", 240, 100],
    [1610612738, "BOS", "Boston Celtics", "0022300003", "2023-10-27T00:00:00", "BOS vs. CHI", "W", 240, 112],
    [1610612741, "CHI", "Chicago Bulls", "0022300003", "2023-10-27T00:00:00", "CHI @ BOS", "L", 240, 99],
]

PLAYOFF_ROWS = [
    [1610612738, "BOS", "Boston Celtics", "0042300101", "2024-04-20T00:00:00", "BOS vs. MIA", "W", 240, 114],
    [1610612748, "MIA", "Miami Heat", "0042300101", "2024-04-20T00:00:00", "MIA @ BOS", "L", 240, 94],
]


def test_nba_games_column_schema():
    assert NBA_GAMES_COLUMNS == [
        "game_id",
        "date",
        "season",
        "game_type",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "home_win",
        "home_rest_days",
        "away_rest_days",
        "home_b2b",
        "away_b2b",
    ]


def test_normalize_nba_team_aliases():
    assert ni._normalize_team_name("LA Clippers") == "LA Clippers"
    assert ni._normalize_team_name("Los Angeles Clippers") == "LA Clippers"


def test_parse_team_rows_final_scores():
    rows = ni._result_set_rows(_payload(REGULAR_ROWS[:2]))
    games, skipped = ni._parse_team_rows(
        rows, season=2024, season_label="2023-24", game_type="regular"
    )
    assert skipped == 0
    assert len(games) == 1
    game = games[0]
    assert game.home_team == "Boston Celtics"
    assert game.away_team == "New York Knicks"
    assert game.home_score == 108
    assert game.away_score == 104
    assert game.game_type == "regular"
    assert game.home_score > game.away_score


def test_parse_team_rows_playoff_type():
    rows = ni._result_set_rows(_payload(PLAYOFF_ROWS))
    games, _ = ni._parse_team_rows(
        rows, season=2024, season_label="2023-24", game_type="playoff"
    )
    assert len(games) == 1
    assert games[0].game_type == "playoff"
    assert games[0].game_id.startswith("004")


def test_rest_days_and_b2b_on_fixture():
    games = [
        ni.ParsedGame(
            "0022300001", "2023-10-24", 2024, "2023-24", "regular",
            "Boston Celtics", "New York Knicks", 108, 104,
        ),
        ni.ParsedGame(
            "0022300002", "2023-10-26", 2024, "2023-24", "regular",
            "Boston Celtics", "Miami Heat", 110, 100,
        ),
        ni.ParsedGame(
            "0022300003", "2023-10-27", 2024, "2023-24", "regular",
            "Boston Celtics", "Chicago Bulls", 112, 99,
        ),
    ]
    df = ni._games_to_frame(games)
    rest_fill = 2.0
    out = ni._compute_rest_and_b2b(df, rest_fill)
    by_id = out.set_index("game_id")

    # First BOS game of season -> rest_fill
    assert by_id.loc["0022300001", "home_rest_days"] == rest_fill
    assert by_id.loc["0022300001", "home_b2b"] == 0

    # Oct 26: 2 days after Oct 24
    assert by_id.loc["0022300002", "home_rest_days"] == 2.0
    assert by_id.loc["0022300002", "home_b2b"] == 0

    # Oct 27: B2B after Oct 26
    assert by_id.loc["0022300003", "home_rest_days"] == 1.0
    assert by_id.loc["0022300003", "home_b2b"] == 1


def test_home_win_computed():
    games = [
        ni.ParsedGame(
            "0022300001", "2023-10-24", 2024, "2023-24", "regular",
            "Boston Celtics", "New York Knicks", 108, 104,
        ),
    ]
    df = ni._games_to_frame(games)
    assert int(df.iloc[0]["home_win"]) == 1


@patch("app.ingest.nba.SEASONS", ((2024, "2023-24"),))
@patch("app.ingest.nba.write_outputs")
@patch("app.ingest.nba._fetch_season_type")
def test_build_modeling_table_mocked(mock_fetch, mock_write):
    def _side_effect(season_label, season_type):
        rows = REGULAR_ROWS if season_type == "Regular Season" else PLAYOFF_ROWS
        return ni._result_set_rows(_payload(rows))

    mock_fetch.side_effect = _side_effect
    df = ni.build_modeling_table()
    mock_write.assert_not_called()
    assert len(df) >= 3
    assert set(df["game_type"]) >= {"regular", "playoff"}
    assert df["home_win"].isin([0, 1]).all()
    assert df["home_rest_days"].notna().all()
    assert df["home_b2b"].isin([0, 1]).all()
    assert list(df.columns) == NBA_GAMES_COLUMNS


@patch("app.ingest.nba.fetch_raw_games")
@patch("app.ingest.nba.write_outputs")
def test_run_ingest_calls_write(mock_write, mock_fetch):
    mock_fetch.return_value = [
        ni.ParsedGame(
            "0022300001", "2023-10-24", 2024, "2023-24", "regular",
            "Boston Celtics", "New York Knicks", 108, 104,
        ),
    ]
    df = ni.run_ingest()
    mock_write.assert_called_once()
    assert len(df) == 1
