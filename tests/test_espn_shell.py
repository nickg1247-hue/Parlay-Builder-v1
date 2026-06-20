"""Tests for ESPN-shell features: lineups, teams, news images."""

from unittest.mock import patch

from app.services import news_feed
from app.services.mlb_game_lineup import clear_lineup_cache, get_mlb_game_lineup
from app.services.teams_hub import group_roster_by_position


def test_image_from_item_media_content():
    xml = """
    <item>
      <title>Test</title>
      <link>https://example.com/a</link>
      <media:content url="https://cdn.example.com/photo.jpg" xmlns:media="http://search.yahoo.com/mrss/" />
    </item>
    """
    import xml.etree.ElementTree as ET

    item = ET.fromstring(xml)
    assert news_feed._image_from_item(item) == "https://cdn.example.com/photo.jpg"


def test_image_from_description_img():
    xml = """
    <item>
      <title>Test</title>
      <link>https://example.com/b</link>
      <description>&lt;img src="https://cdn.example.com/hit.jpg" /&gt;</description>
    </item>
    """
    import xml.etree.ElementTree as ET

    item = ET.fromstring(xml)
    assert news_feed._image_from_item(item) == "https://cdn.example.com/hit.jpg"


def test_group_roster_football_positions():
    roster = [
        {"name": "QB One", "position": "QB", "jersey": 1},
        {"name": "WR Alpha", "position": "WR", "jersey": 11},
        {"name": "WR Beta", "position": "WR", "jersey": 12},
        {"name": "LT Block", "position": "LT", "jersey": 75},
    ]
    groups = group_roster_by_position(roster, "cfb")
    labels = [g["label"] for g in groups]
    assert "Quarterbacks" in labels
    assert "Wide Receivers" in labels
    assert "Offensive Line" in labels
    wr_group = next(g for g in groups if g["key"] == "WR")
    assert len(wr_group["players"]) == 2


@patch("app.services.mlb_game_lineup._fetch_boxscore")
@patch("app.services.mlb_game_lineup._fetch_schedule_game")
@patch("app.services.mlb_game_lineup._fetch_person_stats")
def test_mlb_game_lineup_probable_pitchers(mock_stats, mock_sched, mock_box):
    clear_lineup_cache()
    mock_stats.return_value = {
        592789: {
            "hitting": {},
            "pitching": {
                "era": "2.50",
                "wins": 5,
                "losses": 2,
                "strikeOuts": 80,
                "whip": "0.95",
            },
        },
    }
    mock_sched.return_value = {
        "gamePk": 123,
        "teams": {
            "away": {
                "probablePitcher": {"id": 592789, "fullName": "Ace Pitcher"},
            },
            "home": {"probablePitcher": {}},
        },
    }
    mock_box.return_value = None

    payload = get_mlb_game_lineup("123")
    assert payload["away"]["starting_pitcher"]["name"] == "Ace Pitcher"
    assert payload["away"]["starting_pitcher"]["stats"]["era"] == "2.50"
