"""Free mode (USE_LIVE_ODDS=false) — no live sportsbook API calls."""

from datetime import date
from unittest.mock import patch

from app.odds import the_odds_api as odds_api
from app.odds.live_odds import live_odds_enabled
from app.parlay.ev_ranker import attach_market_odds
from app.services.game_insights import _sportsbook_lines
import pandas as pd


def test_live_odds_disabled_by_default():
    with patch.dict("os.environ", {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "false"}, clear=False):
        assert live_odds_enabled() is False


def test_live_odds_enabled_when_flag_and_key():
    with patch.dict(
        "os.environ",
        {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "true"},
        clear=False,
    ):
        assert live_odds_enabled() is True


@patch.dict("os.environ", {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "false"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_fetch_skips_when_free_mode(mock_fetch):
    odds_api.clear_odds_cache()
    result = odds_api.fetch_mlb_odds(include_totals=True)
    assert result is None
    mock_fetch.assert_not_called()


@patch.dict("os.environ", {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "false"}, clear=False)
@patch("app.odds.odds_repository.fetch_from_api_if_allowed")
def test_attach_market_odds_skips_api_in_free_mode(mock_gate):
    slate = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2026-06-06",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
            }
        ]
    )
    merged, source = attach_market_odds(slate, date(2026, 6, 6), use_cache=False)
    mock_gate.assert_not_called()
    assert source == "none"


@patch.dict("os.environ", {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_from_api_if_allowed")
def test_attach_market_odds_reads_repo_without_force_refresh(mock_gate):
    from app.odds.odds_repository import save_date
    import tempfile
    import os

    game_date = date.today()
    payload = {
        "date": game_date.isoformat(),
        "fetched_at": "2026-06-06T12:00:00+00:00",
        "source": "the_odds_api_live",
        "games": [
            {
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "commence_time": f"{game_date.isoformat()}T23:00:00Z",
                "home_ml": -120,
                "away_ml": 110,
                "ou_line": None,
                "over_odds": None,
                "under_odds": None,
                "home_spread_point": None,
                "home_spread_american": None,
                "away_spread_point": None,
                "away_spread_american": None,
            }
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        os.environ["ODDS_REPOSITORY_DIR"] = td
        save_date(game_date, payload)
        slate = pd.DataFrame(
            [
                {
                    "game_id": "1",
                    "date": game_date.isoformat(),
                    "home_team": "New York Yankees",
                    "away_team": "Boston Red Sox",
                    "model_prob_home": 0.55,
                }
            ]
        )
        merged, source = attach_market_odds(slate, game_date, use_cache=False)
        mock_gate.assert_not_called()
        assert source == "the_odds_api"
        assert merged.iloc[0]["home_ml"] == -120


@patch.dict("os.environ", {"ODDS_API_KEY": "secret", "USE_LIVE_ODDS": "false"}, clear=False)
@patch("app.odds.odds_repository._do_http_fetch")
def test_fetch_from_api_if_allowed_blocks_http_in_free_mode(mock_http):
    from app.odds import odds_repository as repo

    result = repo.fetch_from_api_if_allowed(date.today())
    assert result.denied is True
    assert result.denied_reason == "live_odds_disabled"
    mock_http.assert_not_called()


@patch.dict("os.environ", {"USE_LIVE_ODDS": "false"}, clear=False)
def test_sportsbook_lines_none_in_free_mode():
    game = {"home_team": "New York Yankees", "away_team": "Boston Red Sox"}
    lines = _sportsbook_lines(game, date(2026, 6, 6), use_cache=False)
    assert lines["source"] == "none"
    assert lines["away_ml"] is None
    assert lines["home_ml"] is None
    assert lines["total_line"] is None
