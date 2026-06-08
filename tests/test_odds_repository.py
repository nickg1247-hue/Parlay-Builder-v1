"""Persistent odds repository tests."""

from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pandas as pd
import pytest

from app.odds import odds_repository as repo
from app.parlay.ev_ranker import attach_market_odds
from app.services import daily_board as db

def _fake_event(game_date: date | None = None) -> dict:
    d = (game_date or date.today()).isoformat()
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
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "price": -110, "point": 8.5},
                        {"name": "Under", "price": -110},
                    ],
                },
            ]
        }
    ],
    }


FAKE_EVENT = _fake_event()


@pytest.fixture
def isolated_repo(tmp_path, monkeypatch):
    root = tmp_path / "odds_repository"
    monkeypatch.setenv("ODDS_REPOSITORY_DIR", str(root))
    repo.clear_repository(root)
    repo.reset_fetch_locks_for_tests()
    yield root
    repo.clear_repository(root)
    repo.reset_fetch_locks_for_tests()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_missing_date_fetches_once_then_reuses_repo(mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    games1, src1 = repo.get_mlb_odds_for_date(game_date)
    games2, src2 = repo.get_mlb_odds_for_date(game_date)

    assert len(games1) == 1
    assert games1[0]["home_ml"] == -120
    assert src1 == "the_odds_api_live"
    assert games2 == games1
    mock_fetch.assert_called_once()
    assert (isolated_repo / f"{game_date.isoformat()}.json").exists()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_existing_date_no_http(mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    repo.get_mlb_odds_for_date(game_date)
    mock_fetch.reset_mock()

    games, src = repo.get_mlb_odds_for_date(game_date)
    assert len(games) == 1
    assert src == "the_odds_api_live"
    mock_fetch.assert_not_called()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_force_refresh_updates_file(mock_fetch, isolated_repo):
    game_date = date.today()
    ev = _fake_event(game_date)
    mock_fetch.side_effect = [
        [ev],
        [
            {
                **ev,
                "bookmakers": [
                    {
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "New York Yankees", "price": -130},
                                    {"name": "Boston Red Sox", "price": 115},
                                ],
                            }
                        ]
                    }
                ],
            }
        ],
    ]

    repo.get_mlb_odds_for_date(game_date)
    first = repo.load_date(game_date)
    repo.get_mlb_odds_for_date(game_date, force_refresh=True, bypass_min_ttl=True)
    second = repo.load_date(game_date)

    assert first["fetched_at"] != second["fetched_at"]
    assert second["games"][0]["home_ml"] == -130
    assert mock_fetch.call_count == 2
    index = repo._load_index()
    entry = next(d for d in index["dates"] if d["date"] == game_date.isoformat())
    assert entry["api_fetch_count"] == 2


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_historical_mlb_odds")
def test_past_date_uses_historical_once(mock_hist, isolated_repo):
    past = date.today() - timedelta(days=30)
    mock_hist.return_value = [_fake_event(past)]
    repo.get_mlb_odds_for_date(past)
    mock_hist.reset_mock()
    games, src = repo.get_mlb_odds_for_date(past)

    assert src == "the_odds_api_historical"
    assert len(games) == 1
    mock_hist.assert_not_called()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_historical_mlb_odds")
def test_past_date_first_fetch_historical(mock_hist, isolated_repo):
    past = date.today() - timedelta(days=30)
    mock_hist.return_value = [_fake_event(past)]
    games, src = repo.get_mlb_odds_for_date(past)

    assert src == "the_odds_api_historical"
    assert len(games) == 1
    mock_hist.assert_called_once()
    assert mock_hist.call_args.kwargs["snapshot_date"] == f"{past.isoformat()}T23:59:00Z"


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_api_failure_returns_stale_repo(mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    repo.get_mlb_odds_for_date(game_date)
    mock_fetch.side_effect = httpx.HTTPStatusError(
        "err",
        request=httpx.Request("GET", "http://test"),
        response=httpx.Response(500),
    )

    games, src = repo.get_mlb_odds_for_date(
        game_date, force_refresh=True, bypass_min_ttl=True
    )
    assert games is not None
    assert len(games) == 1
    assert src == "the_odds_api_live"


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_min_ttl_skips_redundant_force_refresh(mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    repo.get_mlb_odds_for_date(game_date)
    mock_fetch.reset_mock()

    games, src = repo.get_mlb_odds_for_date(game_date, force_refresh=True)
    assert games is not None
    assert src == "the_odds_api_live"
    mock_fetch.assert_not_called()
    assert repo.last_fetch_meta().get("skip_reason") == "min_ttl"


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_attach_market_odds_uses_repo_without_force(mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    repo.get_mlb_odds_for_date(game_date)
    mock_fetch.reset_mock()

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
    mock_fetch.assert_not_called()
    assert source == "the_odds_api"
    assert merged.iloc[0]["home_ml"] == -120


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
@patch("app.services.daily_board._build_slate")
def test_build_daily_board_refresh_forces_odds_update(mock_slate, mock_fetch, isolated_repo):
    game_date = date.today()
    mock_fetch.return_value = [_fake_event(game_date)]
    mock_slate.return_value = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": game_date.isoformat(),
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
                "model_prob_away": 0.45,
                "display_prob_home": 0.55,
            }
        ]
    )
    repo.get_mlb_odds_for_date(game_date)
    mock_fetch.reset_mock()

    with patch.object(repo, "_repository_fresh_enough", return_value=False):
        with patch.object(db, "DAILY_BOARD_CACHE") as mock_cache:
            mock_cache.exists.return_value = False
            with patch("app.services.daily_board._write_cache"):
                with patch("app.services.daily_board._totals_by_game", return_value={}):
                    with patch("app.services.daily_board._status_footer", return_value={}):
                        with patch(
                            "app.services.daily_board.get_active_model_info",
                            return_value={},
                        ):
                            with patch(
                                "app.services.daily_board._slate_rows", return_value=[]
                            ):
                                with patch(
                                    "app.services.daily_board.rank_parlays",
                                    return_value=[],
                                ):
                                    db.build_daily_board(
                                        game_date=game_date,
                                        refresh=True,
                                        skip_totals=True,
                                    )

    mock_fetch.assert_called_once()
