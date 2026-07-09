"""UFC fighter profile tests."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.odds.ufc_fighter_aliases import fighter_slug
from app.services import ufc_fighter_profile as ufp
from app.services import ufc_home_summary as uhs

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_home_chip_cache():
    uhs._CHIP_CACHE = None
    uhs._CHIP_CACHE_AT = 0.0
    yield
    uhs._CHIP_CACHE = None
    uhs._CHIP_CACHE_AT = 0.0


def test_resolve_fighter_by_slug():
    with patch.object(ufp, "_collect_fighter_names") as mock_names:
        mock_names.return_value = {"conor-mcgregor": "Conor McGregor"}
        assert ufp.resolve_fighter_by_slug("conor-mcgregor") == "Conor McGregor"
        assert ufp.resolve_fighter_by_slug("unknown-fighter") is None


@patch("app.services.ufc_fighter_profile.lookup_fighter_media")
@patch("app.services.ufc_fighter_profile._find_next_fight", return_value=None)
@patch("app.services.ufc_fighter_profile.load_fights")
@patch("app.services.ufc_fighter_profile.resolve_fighter_by_slug")
def test_get_ufc_fighter_profile(mock_resolve, mock_load, _mock_next, mock_media):
    import pandas as pd

    mock_resolve.return_value = "Conor McGregor"
    mock_media.return_value = {
        "headshot_url": "https://example.com/head.png",
        "country": "Ireland",
    }
    mock_load.return_value = pd.DataFrame(
        [
            {
                "fight_id": "1",
                "date": "2023-01-01",
                "home_team": "Conor McGregor",
                "away_team": "Nate Diaz",
                "home_win": 1,
                "weight_class": "Welterweight",
                "event_name": "UFC 202",
            },
            {
                "fight_id": "2",
                "date": "2022-01-01",
                "home_team": "Dustin Poirier",
                "away_team": "Conor McGregor",
                "home_win": 1,
                "weight_class": "Lightweight",
                "event_name": "UFC 257",
            },
        ]
    )
    profile = ufp.get_ufc_fighter_profile("conor-mcgregor")
    assert profile is not None
    assert profile["name"] == "Conor McGregor"
    assert profile["career_record"] == "1-1"
    assert profile["elo_rating"] is not None
    assert profile["portrait"]["headshot_url"] == "https://example.com/head.png"
    assert len(profile["weight_class_history"]) == 2


@patch("app.services.ufc_fighter_profile.get_ufc_fighter_profile", return_value=None)
def test_ufc_fighter_api_not_found(mock_profile):
    resp = client.get("/api/ufc/fighter/unknown-fighter")
    assert resp.status_code == 404
    mock_profile.assert_called_once_with("unknown-fighter")


@patch("app.services.ufc_fighter_profile.get_ufc_fighter_profile")
def test_ufc_fighter_api_success(mock_profile):
    mock_profile.return_value = {
        "slug": "conor-mcgregor",
        "name": "Conor McGregor",
        "sport": "ufc",
        "career_record": "22-6",
        "last5_record": "3-2",
        "elo_rating": 1580.0,
        "weight_class_history": [],
        "recent_fights": [],
        "next_fight": None,
        "portrait": {},
    }
    resp = client.get("/api/ufc/fighter/conor-mcgregor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Conor McGregor"
    assert body["elo_rating"] == 1580.0


def test_ufc_fighter_page():
    resp = client.get("/ufc/fighter/conor-mcgregor")
    assert resp.status_code == 200
    assert "ufc_fighter.js" in resp.text
    assert "ntg-shell home-v2" in resp.text


def test_fighter_slug_used_in_profile_href():
    slug = fighter_slug("Conor McGregor")
    assert slug == "conor-mcgregor"
