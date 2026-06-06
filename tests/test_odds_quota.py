"""Odds API quota gate tests."""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
import threading

import httpx
import pytest

from app.odds import odds_repository as repo

FAKE_EVENT = {
    "home_team": "New York Yankees",
    "away_team": "Boston Red Sox",
    "commence_time": "2026-06-06T23:05:00Z",
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
    monkeypatch.setenv("ODDS_API_MAX_PER_HOUR", "20")
    monkeypatch.setenv("ODDS_API_MAX_PER_DAY", "500")
    repo.clear_repository(root)
    repo.set_clock_for_tests(None)
    yield root
    repo.clear_repository(root)
    repo.set_clock_for_tests(None)


def _seed_quota(root: Path, hour_count: int, day_count: int, hour_bucket: str, day: str):
    q = {
        "day": day,
        "day_count": day_count,
        "hour_bucket": hour_bucket,
        "hour_count": hour_count,
        "last_call_at": None,
        "last_denied_at": None,
        "last_denied_reason": None,
    }
    repo.save_quota(q)


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT])
def test_successful_fetch_increments_quota(mock_fetch, isolated_repo):
    game_date = date.today()
    repo.get_mlb_odds_for_date(game_date)

    q = repo.load_quota()
    assert q["hour_count"] == 1
    assert q["day_count"] == 1
    assert q["last_call_at"] is not None


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds")
def test_failed_fetch_does_not_increment(mock_fetch, isolated_repo, monkeypatch):
    monkeypatch.setenv("ODDS_API_MAX_PER_HOUR", "20")
    game_date = date.today()
    mock_fetch.side_effect = httpx.HTTPStatusError(
        "err",
        request=httpx.Request("GET", "http://test"),
        response=httpx.Response(500),
    )

    repo.get_mlb_odds_for_date(game_date)
    q = repo.load_quota()
    assert q["hour_count"] == 0
    assert q["day_count"] == 0


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT])
def test_hour_limit_denies_21st_call(mock_fetch, isolated_repo):
    now = datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: now)
    bucket = now.strftime("%Y-%m-%dT%H")
    day = now.strftime("%Y-%m-%d")
    _seed_quota(isolated_repo, hour_count=20, day_count=20, hour_bucket=bucket, day=day)

    games, source = repo.get_mlb_odds_for_date(date(2026, 6, 6), force_refresh=True)
    q = repo.load_quota()

    assert games is None or source in ("repository", "none", "the_odds_api_live")
    assert q["hour_count"] == 20
    mock_fetch.assert_not_called()
    meta = repo.last_fetch_meta()
    assert meta.get("quota_denied") is True
    assert meta.get("denied_reason") == "hour_limit"


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT])
def test_day_limit_denies_501st(mock_fetch, isolated_repo, monkeypatch):
    monkeypatch.setenv("ODDS_API_MAX_PER_DAY", "500")
    now = datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: now)
    bucket = now.strftime("%Y-%m-%dT%H")
    day = now.strftime("%Y-%m-%d")
    _seed_quota(isolated_repo, hour_count=0, day_count=500, hour_bucket=bucket, day=day)

    repo.get_mlb_odds_for_date(date(2026, 6, 6), force_refresh=True)
    q = repo.load_quota()

    assert q["day_count"] == 500
    mock_fetch.assert_not_called()
    assert repo.last_fetch_meta().get("denied_reason") == "day_limit"


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
def test_quota_resets_on_new_hour(isolated_repo):
    t1 = datetime(2026, 6, 6, 14, 59, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: t1)
    _seed_quota(
        isolated_repo,
        hour_count=20,
        day_count=20,
        hour_bucket=t1.strftime("%Y-%m-%dT%H"),
        day=t1.strftime("%Y-%m-%d"),
    )

    t2 = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: t2)
    q = repo.load_quota()

    assert q["hour_count"] == 0
    assert q["day_count"] == 20


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
def test_quota_resets_on_new_day(isolated_repo):
    t1 = datetime(2026, 6, 6, 23, 0, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: t1)
    _seed_quota(
        isolated_repo,
        hour_count=5,
        day_count=100,
        hour_bucket=t1.strftime("%Y-%m-%dT%H"),
        day=t1.strftime("%Y-%m-%d"),
    )

    t2 = datetime(2026, 6, 7, 0, 0, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: t2)
    q = repo.load_quota()

    assert q["hour_count"] == 0
    assert q["day_count"] == 0


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT])
def test_denied_returns_stale_repo(mock_fetch, isolated_repo):
    game_date = date.today()
    with patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT]):
        repo.get_mlb_odds_for_date(game_date)

    now = datetime.now(timezone.utc)
    repo.set_clock_for_tests(lambda: now)
    _seed_quota(
        isolated_repo,
        hour_count=20,
        day_count=1,
        hour_bucket=now.strftime("%Y-%m-%dT%H"),
        day=now.strftime("%Y-%m-%d"),
    )

    games, source = repo.get_mlb_odds_for_date(game_date, force_refresh=True)
    assert games is not None
    assert len(games) == 1
    mock_fetch.assert_not_called()


@patch.dict("os.environ", {"ODDS_API_KEY": "test-key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.odds.odds_repository.fetch_live_mlb_odds", return_value=[FAKE_EVENT])
def test_concurrent_acquire_respects_hour_limit(mock_fetch, isolated_repo, monkeypatch):
    monkeypatch.setenv("ODDS_API_MAX_PER_HOUR", "2")
    now = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)
    repo.set_clock_for_tests(lambda: now)
    game_date = date(2026, 6, 6)
    results: list[bool] = []

    def worker():
        r = repo.fetch_from_api_if_allowed(game_date)
        results.append(not r.denied and r.events is not None)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 2
    q = repo.load_quota()
    assert q["hour_count"] == 2
