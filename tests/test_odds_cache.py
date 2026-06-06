"""Repository-backed odds fetch tests (replaces in-memory TTL cache)."""

from datetime import date
from unittest.mock import patch

import pytest

from app.odds import odds_repository as repo
from app.odds import the_odds_api as odds_api


def _fake_event():
    d = date.today().isoformat()
    return {
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "commence_time": f"{d}T23:05:00Z",
        "bookmakers": [
            {
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Yankees", "price": -120},
                            {"name": "Boston Red Sox", "price": 110},
                        ],
                    }
                ]
            }
        ],
    }


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    root = tmp_path / "odds_repository"
    monkeypatch.setenv("ODDS_REPOSITORY_DIR", str(root))
    repo.clear_repository(root)
    yield root
    repo.clear_repository(root)


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"})
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[_fake_event()])
def test_fetch_mlb_odds_uses_repository_not_repeat_http(mock_fetch, isolated_repo):
    first = odds_api.fetch_mlb_odds(include_totals=True, include_spreads=True)
    second = odds_api.fetch_mlb_odds(include_totals=True, include_spreads=True)

    assert first is not None
    assert len(first) == 1
    assert second == first
    mock_fetch.assert_called_once()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"})
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[_fake_event()])
def test_fetch_mlb_odds_force_refresh_calls_api_again(mock_fetch, isolated_repo):
    odds_api.fetch_mlb_odds(include_totals=True)
    odds_api.fetch_mlb_odds(include_totals=True, force_refresh=True)
    assert mock_fetch.call_count == 2


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"})
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_insights_two_games_one_odds_http_call(mock_fetch, isolated_repo):
    from app.services.game_insights import _sportsbook_lines

    mock_fetch.return_value = [
        _fake_event(),
        {
            "home_team": "Chicago Cubs",
            "away_team": "St. Louis Cardinals",
            "commence_time": f"{date.today().isoformat()}T23:05:00Z",
            "bookmakers": [
                {
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Chicago Cubs", "price": -110},
                                {"name": "St. Louis Cardinals", "price": 100},
                            ],
                        }
                    ]
                }
            ],
        },
    ]
    game_a = {"home_team": "New York Yankees", "away_team": "Boston Red Sox"}
    game_b = {"home_team": "Chicago Cubs", "away_team": "St. Louis Cardinals"}
    today = date.today()

    _sportsbook_lines(game_a, today, use_cache=False)
    _sportsbook_lines(game_b, today, use_cache=False)

    mock_fetch.assert_called_once()
