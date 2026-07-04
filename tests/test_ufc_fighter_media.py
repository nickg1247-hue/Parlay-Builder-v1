"""Tests for UFC fighter media enrichment."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from app.services.ufc_fighter_media import enrich_fight_media, headshot_url


def test_headshot_url():
    assert headshot_url(4686725) == (
        "https://a.espncdn.com/i/headshots/mma/players/full/4686725.png"
    )
    assert headshot_url(None) is None


@patch("app.services.scores_ufc.fetch_ufc_scoreboard_day")
def test_enrich_fight_media(mock_fetch):
    from app.services import ufc_fighter_media as ufm

    ufm._MEDIA_CACHE.clear()
    mock_fetch.return_value = [
        {
            "competitions": [
                {
                    "competitors": [
                        {
                            "id": "123",
                            "order": 2,
                            "athlete": {
                                "displayName": "Johnny Walker",
                                "flag": {
                                    "href": "https://a.espncdn.com/i/teamlogos/countries/500/bra.png",
                                    "alt": "Brazil",
                                },
                            },
                        },
                        {
                            "id": "456",
                            "order": 1,
                            "athlete": {
                                "displayName": "Magomed Ankalaev",
                                "flag": {
                                    "href": "https://a.espncdn.com/i/teamlogos/countries/500/rus.png",
                                    "alt": "Russia",
                                },
                            },
                        },
                    ]
                }
            ]
        }
    ]
    fight = {
        "home_team": "Magomed Ankalaev",
        "away_team": "Johnny Walker",
    }
    out = enrich_fight_media(fight, date(2024, 1, 13))
    assert out["home_headshot_url"].endswith("/456.png")
    assert "bra.png" in (out["away_flag_url"] or "")
    assert out["away_flag_backdrop_url"] == "https://flagcdn.com/w1280/br.png"
    assert out["home_flag_backdrop_url"] == "https://flagcdn.com/w1280/ru.png"
    assert out["away_country_code"] == "BR"
    assert out["home_country_code"] == "RU"
