"""CFB schedule resolver and auto-advance tests."""

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_cfb as sc
from app.services.cfb_game_metadata import merge_game_records
from app.services.schedule_cfb import get_cfb_schedule, resolve_cfb_slate_date
from app.services.scores_cfb import fetch_lower_division_scores_day, ncaa_game_record

client = TestClient(app)

ESPN_EVENT = {
    "id": "401635525",
    "date": "2024-11-30T17:00Z",
    "competitions": [
        {
            "date": "2024-11-30T17:00Z",
            "status": {"type": {"state": "pre", "description": "Scheduled"}, "period": 0},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {
                        "id": "61",
                        "displayName": "Georgia Bulldogs",
                        "abbreviation": "UGA",
                        "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/61.png",
                    },
                    "records": [{"name": "overall", "summary": "10-1"}],
                },
                {
                    "homeAway": "away",
                    "team": {
                        "id": "2",
                        "displayName": "Georgia Tech Yellow Jackets",
                        "abbreviation": "GT",
                        "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/2.png",
                    },
                    "records": [{"name": "overall", "summary": "7-4"}],
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def clear_cfb_cache(tmp_path, monkeypatch):
    sc.clear_scores_cache()
    monkeypatch.setattr(
        "app.services.schedule_cfb.fetch_espn_all_scores_day",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.schedule_cfb.fetch_lower_division_scores_day",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(
        "app.services.schedule_cfb.schedule_cache_path",
        lambda d: tmp_path / f"cfb_schedule_{d.isoformat()}.json",
    )
    yield
    sc.clear_scores_cache()


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_resolve_cfb_slate_date_advances_when_today_empty(mock_fetch):
    start = date.today()

    def side_effect(game_date: date):
        offset = (game_date - start).days
        if offset == 3:
            return [ESPN_EVENT]
        return []

    mock_fetch.side_effect = side_effect
    resolved, days_ahead = resolve_cfb_slate_date(start)
    assert resolved == start + timedelta(days=3)
    assert days_ahead == 3


def test_resolve_cfb_slate_date_same_day_when_ingest_has_games():
    start = date(2024, 11, 30)
    resolved, days_ahead = resolve_cfb_slate_date(start)
    assert resolved == start
    assert days_ahead == 0


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_get_cfb_schedule_returns_logos(mock_fetch):
    future = date.today() + timedelta(days=30)
    mock_fetch.return_value = [ESPN_EVENT]
    payload = get_cfb_schedule(future, auto_resolve=False)
    assert payload["games_count"] == 1
    game = payload["games"][0]
    assert game["home_logo_url"]
    assert game["away_logo_url"]
    assert game["home_record"] == "10-1"


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_api_schedule_cfb_stays_on_requested_day(mock_fetch):
    mock_fetch.return_value = []
    resp = client.get("/api/schedule/cfb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_advanced"] is False
    assert data["days_ahead"] == 0


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_schedule_cache_hit(mock_fetch, tmp_path):
    game_date = date(2024, 11, 30)
    cache_path = tmp_path / f"cfb_schedule_{game_date.isoformat()}.json"
    cache_path.write_text(
        json.dumps(
            {
                "date": game_date.isoformat(),
                "sport": "cfb",
                "games": [sc.live_game_record(ESPN_EVENT)],
                "games_count": 1,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    payload = get_cfb_schedule(game_date, auto_resolve=False)
    assert payload["source"] == "cache"
    mock_fetch.assert_not_called()


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
@patch("app.services.schedule_cfb.games_from_ingest")
def test_past_date_uses_ingest_and_saves_cache(mock_ingest, mock_fetch, tmp_path):
    past = date(2020, 1, 1)
    mock_ingest.return_value = [
        {
            "sport": "cfb",
            "game_id": "999",
            "home_team": "Alabama",
            "away_team": "Auburn",
            "home_score": 28,
            "away_score": 14,
            "status": "Final",
        }
    ]
    payload = get_cfb_schedule(past, auto_resolve=False)
    assert payload["source"] == "ingest"
    assert payload["games_count"] == 1
    mock_fetch.assert_not_called()
    cache_path = tmp_path / f"cfb_schedule_{past.isoformat()}.json"
    assert cache_path.exists()

    payload2 = get_cfb_schedule(past, auto_resolve=False)
    assert payload2["source"] == "ingest"
    mock_fetch.assert_not_called()
    mock_ingest.assert_called_once()


def test_cfb_slate_page():
    resp = client.get("/cfb")
    assert resp.status_code == 200
    assert "College Football" in resp.text
    assert "/api/cfb/predictions" in resp.text
    assert "slate-date-input" in resp.text
    for control in ("slate-team-filter", "slate-division-filter", "slate-conference-filter", "slate-ranking-filter"):
        assert control in resp.text
    assert "slate-hbcu-filter" not in resp.text
    board=client.get("/cfb/board")
    assert "FCS Beta" in board.text
    assert "never receive BAM or high-confidence labels" in board.text
def test_ncaa_lower_division_parser_preserves_metadata():
    raw = {
        "game": {
            "gameID": "fcs-101",
            "startDate": "2026-08-29T17:30:00Z",
            "status": "Preview",
            "network": "ESPN+",
            "home": {
                "names": {"short": "Howard Bison"},
                "conference": "MEAC",
                "rank": 12,
            },
            "away": {
                "names": {"short": "Jackson State Tigers"},
                "conference": "SWAC",
                "rank": 19,
            },
        }
    }
    game = ncaa_game_record(raw, "fcs")
    assert game["division"] == "fcs"
    assert game["divisions"] == ["fcs"]
    assert game["home_conference"] == "MEAC"
    assert game["away_conference"] == "SWAC"
    assert game["home_rank"] == 12
    assert game["away_rank"] == 19
    assert game["start_time_utc"] == "2026-08-29T17:30:00Z"
    assert game["model_eligible"] is False
    assert game["model_family"] == "fcs_moneyline"
    assert game["neutral_site_known"] is False
    assert game["neutral_site_missing"] is True

def test_espn_game_marks_explicit_neutral_site_as_known():
    event=dict(ESPN_EVENT);event["competitions"]=[dict(ESPN_EVENT["competitions"][0])];event["competitions"][0]["neutralSite"]=True
    game=sc.live_game_record(event)
    assert game["neutral_site"]==1
    assert game["neutral_site_known"] is True
    assert game["neutral_site_source"]=="espn"


def test_duplicate_fbs_fcs_merge_retains_divisions_sources_and_model_eligibility():
    common = {
        "home_team": "Montana Grizzlies",
        "away_team": "Oregon Ducks",
        "start_time_utc": "2026-08-29T20:00:00Z",
    }
    merged = merge_game_records(
        [
            {**common, "game_id": "espn-1", "division": "fbs", "source": "espn"},
            {**common, "game_id": "ncaa-1", "division": "fcs", "source": "ncaa"},
        ]
    )
    assert len(merged) == 1
    assert merged[0]["divisions"] == ["fbs", "fcs"]
    assert merged[0]["sources"] == ["espn", "ncaa"]
    assert merged[0]["model_eligible"] is False
    assert merged[0]["model_family"] is None


@patch("app.services.schedule_cfb.fetch_lower_division_scores_day")
@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_combined_schedule_includes_espn_fbs_and_ncaa_fcs_metadata(mock_fbs, mock_lower):
    future = date.today() + timedelta(days=30)
    event = dict(ESPN_EVENT)
    event["date"] = future.isoformat() + "T17:00:00Z"
    event["competitions"] = [dict(ESPN_EVENT["competitions"][0])]
    event["competitions"][0]["date"] = event["date"]
    mock_fbs.return_value = [event]
    mock_lower.return_value = (
        [
            ncaa_game_record(
                {
                    "game": {
                        "gameID": "fcs-202",
                        "startDate": future.isoformat() + "T19:00:00Z",
                        "home": {"name": "Howard Bison", "conference": "MEAC"},
                        "away": {"name": "Jackson State Tigers", "conference": "SWAC"},
                    }
                },
                "fcs",
            )
        ],
        [],
    )
    payload = get_cfb_schedule(future, auto_resolve=False, force_live=True)
    assert payload["source"] == "espn+ncaa"
    assert payload["games_count"] == 2
    assert {game["division"] for game in payload["games"]} == {"fbs", "fcs"}
    lower = next(game for game in payload["games"] if game["division"] == "fcs")
    assert lower["home_conference"] == "MEAC"
    assert lower["away_conference"] == "SWAC"
    assert lower["model_eligible"] is False
    assert payload["coverage"]["source_counts"]["espn_fbs"] == 1
    assert payload["coverage"]["source_counts"]["ncaa_fcs"] == 1

@patch("app.services.scores_cfb.httpx.Client")
def test_lower_division_fetch_is_date_complete_across_adjacent_weeks(mock_client):
    def game(game_id, away, home, away_conf, home_conf, *, rank=None):
        return {
            "game": {
                "gameID": game_id,
                "startDate": "08/27/2026",
                "startTime": "6:00 PM ET",
                "gameState": "pre",
                "away": {
                    "names": {"short": away},
                    "conferences": [{"conferenceSeo": away_conf, "conferenceName": ""}],
                },
                "home": {
                    "names": {"short": home},
                    "rank": str(rank or ""),
                    "conferences": [{"conferenceSeo": home_conf, "conferenceName": ""}],
                },
            }
        }

    mercyhurst = game("6604373", "Mercyhurst", "Youngstown St.", "nec", "mvfc", rank=9)
    payloads = {
        ("fcs", 0): [mercyhurst],
        ("fcs", 1): [
            mercyhurst,
            game("fcs-hbcu", "Howard", "Delaware St.", "meac", "meac"),
        ],
        ("fbs", 1): [game("fbs-1", "Temple", "Rutgers", "american", "big-ten")],
    }

    class Response:
        def __init__(self, games):
            self._games = games

        def raise_for_status(self):
            return None

        def json(self):
            return {"games": self._games}

    class Client:
        def __init__(self):
            self.urls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            self.urls.append(url)
            parts = url.rstrip("/").split("/")
            division = parts[-4]
            week = int(parts[-2])
            return Response(payloads.get((division, week), []))

    client_instance = Client()
    mock_client.return_value = client_instance
    games, warnings = fetch_lower_division_scores_day(
        date(2026, 8, 27), season=2026, week=0
    )

    assert warnings == []
    assert len(games) == 3
    assert {game["division"] for game in games} == {"fbs", "fcs"}
    sentinel = next(game for game in games if game["game_id"] == "6604373")
    assert sentinel["away_team"] == "Mercyhurst"
    assert sentinel["home_team"] == "Youngstown St."
    assert sentinel["start_time_utc"] == "2026-08-27T22:00:00Z"
    assert sentinel["home_rank"] == 9
    assert sentinel["away_conference"] == "NEC"
    assert sentinel["home_conference"] == "MVFC"
    assert sentinel["model_eligible"] is False
    assert any("/00/all-conf" in url for url in client_instance.urls)
    assert any("/01/all-conf" in url for url in client_instance.urls)
    assert any("/02/all-conf" in url for url in client_instance.urls)

@patch("app.services.scores_cfb.httpx.Client")
def test_fbs_fcs_fetch_reports_partial_source_failure(mock_client):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"games": []}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            if "/fcs/" in url:
                raise httpx.ConnectError("FCS feed unavailable")
            return Response()

    mock_client.return_value = Client()
    games, warnings = fetch_lower_division_scores_day(
        date(2026, 8, 27), season=2026, week=1
    )
    assert games == []
    assert warnings == ["NCAA FCS coverage unavailable."]
