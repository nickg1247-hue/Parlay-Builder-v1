"""In-season power-ranking relearn for NFL and college football winner picks."""

from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.season_relearn import (
    blend_home_probability,
    blend_weight_for_games,
    fit_power_win_model,
    mix_probability,
    power_win_probability,
    walk_forward_samples,
)


def test_short_sample_keeps_the_prior_scale():
    fitted = fit_power_win_model("nfl", [(3.0, 1.0, 1)] * 4)
    assert fitted["fit_source"] == "prior"
    assert fitted["power_coef"] > 0
    assert fitted["games_fit"] == 4


def test_current_season_fit_trusts_a_real_power_gap():
    samples = []
    for _ in range(20):
        samples.append((8.0, 1.0, 1))
        samples.append((-8.0, 1.0, 0))
    fitted = fit_power_win_model("cfb", samples)
    assert fitted["fit_source"] == "current_season"
    assert fitted["power_coef"] > 0
    assert fitted["games_fit"] == 40


def test_better_power_rating_raises_the_home_win_chance():
    even = power_win_probability(0.0, 0.12, 0.28, 0.0, neutral=False)
    favored = power_win_probability(0.0, 0.12, 0.28, 7.0, neutral=False)
    assert favored > even > 0.5


def test_blend_weight_grows_through_the_season_and_stays_capped():
    early = blend_weight_for_games(0)
    mid = blend_weight_for_games(4)
    late = blend_weight_for_games(16)
    assert early < mid < late
    assert late <= 0.62


def test_unranked_teams_are_left_out_of_the_relearn():
    from app.services.season_relearn import _ratings_from_payload

    ratings = _ratings_from_payload(
        "nfl",
        {
            "teams": [
                {"abbr": "KC", "power": 4.0},
                {"abbr": "ARI", "power": None},
            ]
        },
    )
    assert ratings == {"KC": 4.0}


def test_walk_forward_reads_rankings_from_before_that_week():
    frame = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2026-09-10",
                "season": 2026,
                "game_type": "regular",
                "week": 1,
                "home_team_abbr": "KC",
                "away_team_abbr": "CAR",
                "home_score": 27,
                "away_score": 10,
                "neutral_site": 0,
            }
        ]
    )

    def build(day):
        assert day == date(2026, 9, 10)
        return {
            "teams": [
                {"abbr": "KC", "power": 6.0},
                {"abbr": "CAR", "power": -4.0},
            ]
        }

    samples = walk_forward_samples("nfl", frame, date(2026, 9, 22), build)
    assert samples == [(10.0, 1.0, 1)]


def test_mix_moves_the_pick_toward_the_rankings():
    mixed = mix_probability(0.55, 0.80, 0.40)
    assert 0.55 < mixed < 0.80


def test_blend_uses_saved_rankings_for_the_same_season(tmp_path, monkeypatch):
    artifact = {
        "sport": "nfl",
        "applied": True,
        "season": 2026,
        "blend_weight": 0.5,
        "intercept": 0.0,
        "power_coef": 0.12,
        "home_coef": 0.0,
        "ratings": {"KC": 8.0, "CAR": -6.0},
    }
    path = tmp_path / "nfl_season_relearn.json"
    monkeypatch.setattr("app.services.season_relearn.artifact_path", lambda sport: path)
    monkeypatch.setattr("app.services.season_relearn._CACHE", {})
    path.write_text(__import__("json").dumps(artifact), encoding="utf-8")

    mixed, detail = blend_home_probability("nfl", date(2026, 9, 22), "KC", "CAR", False, 0.52)
    assert detail["relearn_applied"] is True
    assert mixed > 0.52

    unchanged, skipped = blend_home_probability("nfl", date(2025, 9, 7), "KC", "CAR", False, 0.52)
    assert skipped is None
    assert unchanged == 0.52


def test_relearn_endpoints_round_trip(monkeypatch):
    def fake_relearn(sport, as_of=None):
        return {
            "applied": True,
            "sport": sport,
            "season": 2026,
            "games_fit": 28 if sport == "nfl" else 90,
            "blend_weight": 0.41,
            "through": "2026-09-21",
            "fit_source": "current_season",
            "teams_ranked": 32,
            "ratings": {"KC": 4.0},
        }

    monkeypatch.setattr("app.services.season_relearn.relearn", fake_relearn)
    monkeypatch.setattr(
        "app.services.season_relearn.relearn_status",
        lambda sport: {"applied": False, "sport": sport},
    )
    client = TestClient(app)
    idle = client.get("/api/nfl/model/relearn")
    assert idle.status_code == 200
    assert idle.json()["applied"] is False

    ran = client.post("/api/nfl/model/relearn")
    assert ran.status_code == 200
    body = ran.json()
    assert body["sport"] == "nfl"
    assert body["games_fit"] == 28
    assert "ratings" not in body

    cfb = client.post("/api/cfb/model/relearn")
    assert cfb.status_code == 200
    assert cfb.json()["sport"] == "cfb"
    assert cfb.json()["games_fit"] == 90
