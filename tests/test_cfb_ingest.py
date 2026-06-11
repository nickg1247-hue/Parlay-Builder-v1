"""CFB ingest tests with mocked CFBD responses."""

from unittest.mock import MagicMock, patch

import pytest

from app.ingest.cfb import _parse_cfbd_row, build_modeling_table, fetch_raw_games

SAMPLE_GAMES = [
    {
        "id": 401403854,
        "season": 2024,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2024-08-31T16:00:00.000Z",
        "homeTeam": "Georgia",
        "awayTeam": "Clemson",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 34,
        "awayPoints": 3,
        "completed": True,
    },
    {
        "id": 401403855,
        "season": 2024,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2024-08-31T19:30:00.000Z",
        "homeTeam": "USC",
        "awayTeam": "LSU",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 27,
        "awayPoints": 20,
        "completed": True,
    },
    {
        "id": 401403856,
        "season": 2024,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2024-09-01T00:00:00.000Z",
        "homeTeam": "Florida State",
        "awayTeam": "Boston College",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": None,
        "awayPoints": None,
        "completed": False,
    },
    {
        "id": 401403857,
        "season": 2024,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2024-09-01T00:00:00.000Z",
        "homeTeam": "Lincoln (CA)",
        "awayTeam": "College of Idaho",
        "homeClassification": "ii",
        "awayClassification": None,
        "homePoints": 14,
        "awayPoints": 7,
        "completed": True,
    },
]


def test_parse_cfbd_row_completed_game():
    parsed = _parse_cfbd_row(SAMPLE_GAMES[0], 2024)
    assert parsed is not None
    assert parsed.home_team == "Georgia"
    assert parsed.away_team == "Clemson"
    assert parsed.home_score == 34
    assert parsed.away_score == 3
    assert parsed.date == "2024-08-31"


def test_parse_cfbd_row_skips_incomplete():
    assert _parse_cfbd_row(SAMPLE_GAMES[2], 2024) is None


def test_parse_cfbd_row_skips_non_fbs():
    assert _parse_cfbd_row(SAMPLE_GAMES[3], 2024) is None


@patch("app.ingest.cfb.httpx.Client")
def test_fetch_raw_games_parses_multiple(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_GAMES
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response

    games = fetch_raw_games(api_key="test-key")
    assert len(games) == 2
    assert all(g.home_team and g.away_team for g in games)


@patch("app.ingest.cfb.fetch_raw_games")
def test_build_modeling_table_has_required_columns(mock_fetch):
    from app.ingest.cfb import ParsedGame

    mock_fetch.return_value = [
        ParsedGame("1", "2024-08-31", 2024, "regular", "Georgia", "Clemson", 34, 3),
        ParsedGame("2", "2024-09-07", 2024, "regular", "Alabama", "USC", 21, 17),
        ParsedGame("3", "2024-09-14", 2024, "regular", "Ohio State", "Michigan", 28, 14),
    ]
    df = build_modeling_table(api_key="test-key")
    assert len(df) == 3
    assert "home_team" in df.columns
    assert "away_team" in df.columns
    assert df["home_team"].notna().all()
    assert df["away_team"].notna().all()


def test_missing_api_key_exits(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="CFBD_API_KEY"):
        fetch_raw_games(api_key=None)
