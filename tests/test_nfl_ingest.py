"""NFL ingest tests with mocked ESPN scoreboard events."""

from unittest.mock import MagicMock, patch

from app.ingest.nfl import (
    ParsedGame,
    build_modeling_table,
    fetch_raw_games,
    is_divisional,
    normalize_abbr,
    parse_espn_event,
    parse_espn_schedule_event,
)

SAMPLE_EVENT = {
    "id": "401671617",
    "date": "2024-09-05T00:20Z",
    "season": {"year": 2024, "type": 2},
    "week": {"number": 1},
    "competitions": [
        {
            "date": "2024-09-05T00:20Z",
            "neutralSite": False,
            "status": {"type": {"state": "post", "completed": True, "description": "Final"}},
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "27",
                    "team": {
                        "id": "12",
                        "displayName": "Kansas City Chiefs",
                        "abbreviation": "KC",
                    },
                },
                {
                    "homeAway": "away",
                    "score": "20",
                    "team": {
                        "id": "13",
                        "displayName": "Baltimore Ravens",
                        "abbreviation": "BAL",
                    },
                },
            ],
        }
    ],
}

PRESEASON_EVENT = {
    **SAMPLE_EVENT,
    "id": "401670001",
    "season": {"year": 2024, "type": 1},
}

TIE_EVENT = {
    **SAMPLE_EVENT,
    "id": "401671699",
    "competitions": [
        {
            **SAMPLE_EVENT["competitions"][0],
            "competitors": [
                {**SAMPLE_EVENT["competitions"][0]["competitors"][0], "score": "20"},
                {**SAMPLE_EVENT["competitions"][0]["competitors"][1], "score": "20"},
            ],
        }
    ],
}


def test_parse_espn_event_completed_regular_season():
    parsed = parse_espn_event(SAMPLE_EVENT, season=2024, week=1)
    assert parsed is not None
    assert parsed.game_id == "401671617"
    assert parsed.home_team == "Kansas City Chiefs"
    assert parsed.away_team == "Baltimore Ravens"
    assert parsed.home_score == 27
    assert parsed.away_score == 20
    assert parsed.date == "2024-09-05"
    assert parsed.season == 2024
    assert parsed.week == 1
    assert parsed.home_team_abbr == "KC"
    assert parsed.away_team_abbr == "BAL"
    assert parsed.divisional == 0
    assert parsed.game_type == "regular"


def test_parse_espn_event_keeps_preseason():
    parsed = parse_espn_event(PRESEASON_EVENT, season=2024, week=1)
    assert parsed is not None
    assert parsed.game_id == "401670001"
    assert parsed.game_type == "preseason"


def test_parse_espn_event_skips_playoffs():
    playoff = {**SAMPLE_EVENT, "id": "401679999", "season": {"year": 2024, "type": 3}}
    assert parse_espn_event(playoff, season=2024, week=1) is None


def test_parse_espn_event_reads_scoreboard_odds():
    event = {
        **SAMPLE_EVENT,
        "competitions": [
            {
                **SAMPLE_EVENT["competitions"][0],
                "odds": [
                    {
                        "spread": -3.5,
                        "overUnder": 47.5,
                        "homeTeamOdds": {"moneyLine": -165},
                        "awayTeamOdds": {"moneyLine": 140},
                    }
                ],
            }
        ],
    }
    parsed = parse_espn_event(event, season=2024, week=1)
    assert parsed is not None
    assert parsed.espn_home_ml == -165
    assert parsed.espn_away_ml == 140
    assert parsed.espn_spread == -3.5
    assert parsed.espn_ou == 47.5


def test_parse_espn_event_skips_ties():
    assert parse_espn_event(TIE_EVENT, season=2024, week=1) is None


def test_parse_espn_schedule_event_keeps_unplayed():
    scheduled = {
        **SAMPLE_EVENT,
        "id": "401772001",
        "competitions": [
            {
                **SAMPLE_EVENT["competitions"][0],
                "status": {"type": {"state": "pre", "completed": False}},
                "competitors": [
                    {
                        **SAMPLE_EVENT["competitions"][0]["competitors"][0],
                        "score": "0",
                        "team": {
                            **SAMPLE_EVENT["competitions"][0]["competitors"][0]["team"],
                            "logo": "https://example.com/kc.png",
                        },
                    },
                    {
                        **SAMPLE_EVENT["competitions"][0]["competitors"][1],
                        "score": "0",
                    },
                ],
            }
        ],
    }
    assert parse_espn_event(scheduled, season=2024, week=1) is None
    parsed = parse_espn_schedule_event(scheduled, season=2024, week=1)
    assert parsed is not None
    assert parsed["game_id"] == "401772001"
    assert parsed["completed"] is False
    assert parsed["home_win"] is None
    assert parsed["home_team_abbr"] == "KC"
    assert parsed["away_team_abbr"] == "BAL"
    assert parsed["home_logo_url"] == "https://example.com/kc.png"


def test_parse_espn_schedule_event_skips_preseason():
    assert parse_espn_schedule_event(PRESEASON_EVENT, season=2024, week=1) is None


def test_espn_game_id_stable_across_week_and_day_payloads():
    week_parsed = parse_espn_event(SAMPLE_EVENT, season=2024, week=1)
    day_parsed = parse_espn_event(SAMPLE_EVENT)
    assert week_parsed is not None and day_parsed is not None
    assert week_parsed.game_id == day_parsed.game_id == "401671617"


def test_divisional_flag_afc_north():
    assert is_divisional("BAL", "PIT") == 1
    assert is_divisional("KC", "BAL") == 0
    assert is_divisional("OAK", "LAC") == 1
    assert normalize_abbr("OAK") == "LV"
    assert normalize_abbr("WAS") == "WSH"


@patch("app.ingest.nfl.time.sleep", return_value=None)
@patch("app.ingest.nfl.httpx.Client")
def test_fetch_raw_games_parses_week_scoreboard(mock_client_cls, _sleep):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"events": [SAMPLE_EVENT]}
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response

    games = fetch_raw_games()
    assert len(games) == 1
    assert games[0].game_id == "401671617"
    assert mock_client.get.call_count == 7 * (4 + 18)


@patch("app.ingest.nfl.fetch_raw_games")
def test_build_modeling_table_has_required_columns(mock_fetch):
    mock_fetch.return_value = [
        ParsedGame(
            "1",
            "2024-09-05",
            2024,
            1,
            "regular",
            "Kansas City Chiefs",
            "Baltimore Ravens",
            "12",
            "33",
            "KC",
            "BAL",
            27,
            20,
            0,
            0,
        ),
        ParsedGame(
            "2",
            "2024-09-08",
            2024,
            1,
            "regular",
            "Buffalo Bills",
            "Miami Dolphins",
            "2",
            "15",
            "BUF",
            "MIA",
            31,
            10,
            1,
            0,
        ),
        ParsedGame(
            "3",
            "2024-09-12",
            2024,
            2,
            "regular",
            "Kansas City Chiefs",
            "Cincinnati Bengals",
            "12",
            "4",
            "KC",
            "CIN",
            26,
            25,
            0,
            0,
        ),
    ]
    df = build_modeling_table()
    assert len(df) == 3
    assert df["game_id"].tolist() == ["1", "2", "3"]
    assert "home_rest_days" in df.columns
    assert "away_rest_days" in df.columns
    kc_week2 = df[df["game_id"] == "3"].iloc[0]
    assert kc_week2["home_rest_days"] == 7.0
