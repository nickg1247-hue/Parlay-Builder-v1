"""Power ranking math: opponent adjustment, prior fade, and published sub-points."""

from datetime import date

import pandas as pd

from fastapi.testclient import TestClient

from app.main import app
from app.services.nfl_power_rankings import build_nfl_power_rankings
from app.services.power_rankings import RawGame, prior_weight_for_games, rate_games


def _game(team, opponent, pf, pa, *, when, home=True):
    return RawGame(
        team=team,
        opponent=opponent,
        points_for=pf,
        points_against=pa,
        is_home=home,
        neutral=True,
        when=float(when),
        win=int(pf > pa),
    )


def _pair(home, away, home_score, away_score, when):
    return [
        _game(home, away, home_score, away_score, when=when, home=True),
        _game(away, home, away_score, home_score, when=when, home=False),
    ]


def test_cycle_stays_near_average():
    games = []
    games += _pair("A", "B", 24, 20, 1)
    games += _pair("B", "C", 24, 20, 2)
    games += _pair("C", "A", 24, 20, 3)
    ratings = rate_games(games, {"A", "B", "C"}, hfa=0, margin_cap=28)
    for team in ("A", "B", "C"):
        assert abs(ratings[team].offense) < 1.5
        assert abs(ratings[team].defense) < 1.5


def test_scoring_on_a_good_defense_rates_higher():
    games = []
    games += _pair("GOOD", "AVG", 17, 14, 1)
    games += _pair("AVG", "BAD", 35, 14, 2)
    games += _pair("T1", "GOOD", 24, 17, 3)
    games += _pair("T2", "BAD", 24, 17, 4)
    ratings = rate_games(
        games,
        {"GOOD", "AVG", "BAD", "T1", "T2"},
        hfa=0,
        margin_cap=50,
    )
    assert ratings["T1"].offense > ratings["T2"].offense


def test_margin_cap_limits_a_blowout():
    from app.services.power_rankings import cap_scores

    capped_for, capped_against = cap_scores(70, 0, 21)
    assert capped_for - capped_against == 21
    assert capped_for + capped_against == 70

    games = []
    games += _pair("A", "B", 70, 0, 1)
    games += _pair("A", "C", 28, 14, 2)
    games += _pair("A", "D", 28, 14, 3)
    games += _pair("C", "B", 24, 17, 4)
    games += _pair("D", "B", 24, 17, 5)
    games += _pair("C", "D", 21, 20, 6)
    open_cap = rate_games(games, {"A", "B", "C", "D"}, hfa=0, margin_cap=80)
    capped = rate_games(games, {"A", "B", "C", "D"}, hfa=0, margin_cap=21)
    assert open_cap["A"].offense > capped["A"].offense


def test_prior_fades_as_games_accumulate():
    early = prior_weight_for_games(1, late_weight=0.30, late_games=8)
    late = prior_weight_for_games(8, late_weight=0.30, late_games=8)
    assert early > 0.65
    assert late == 0.30
    assert prior_weight_for_games(0, late_weight=0.15, late_games=12) > early


def test_nfl_rankings_ignore_future_games_and_sum_subpoints():
    rows = []
    for week, home, away, home_score, away_score in (
        (1, "KC", "BUF", 27, 20),
        (2, "BUF", "KC", 24, 21),
        (3, "PHI", "DAL", 31, 17),
        (4, "DAL", "PHI", 20, 17),
    ):
        rows.append(
            {
                "date": date(2025, 9, week),
                "season": 2025,
                "game_type": "regular",
                "home_team": home,
                "away_team": away,
                "home_team_abbr": home,
                "away_team_abbr": away,
                "home_score": home_score,
                "away_score": away_score,
                "neutral_site": 1,
            }
        )
    rows.append(
        {
            "date": date(2025, 9, 10),
            "season": 2025,
            "game_type": "regular",
            "home_team": "KC",
            "away_team": "BUF",
            "home_team_abbr": "KC",
            "away_team_abbr": "BUF",
            "home_score": 49,
            "away_score": 0,
            "neutral_site": 1,
        }
    )
    frame = pd.DataFrame(rows)
    payload = build_nfl_power_rankings(as_of=date(2025, 9, 10), games=frame)
    assert payload["team_count"] == 32
    assert payload["season"] == 2025
    by_abbr = {team["abbr"]: team for team in payload["teams"]}
    assert by_abbr["KC"]["games_played"] == 2
    assert by_abbr["NE"]["games_played"] == 0
    assert by_abbr["NE"]["power"] is None
    assert by_abbr["NE"]["rank"] is None
    assert by_abbr["KC"]["prior_weight"] == 0
    assert all(point["key"] != "prior" for point in by_abbr["KC"]["subpoints"])
    played = [team for team in payload["teams"] if team["games_played"]]
    offense_ranks = [team["offense_rank"] for team in played]
    defense_ranks = [team["defense_rank"] for team in played]
    assert sorted(offense_ranks) == list(range(1, len(played) + 1))
    assert sorted(defense_ranks) == list(range(1, len(played) + 1))
    assert payload["ranked_count"] == len(played)
    keys = {item["key"] for item in payload["unavailable"]}
    assert {"quarterback", "passing", "rushing", "special_teams", "availability"} <= keys
    for team in payload["teams"]:
        if team["power"] is None:
            assert team["games_played"] == 0
            assert team["subpoints"] == []
            continue
        counted = [point for point in team["subpoints"] if point["counted"]]
        assert round(sum(point["points"] for point in counted), 1) == team["power"]
        schedule = next(point for point in team["subpoints"] if point["key"] == "schedule")
        recent = next(point for point in team["subpoints"] if point["key"] == "recent")
        assert schedule["counted"] is False
        assert recent["counted"] is False


def test_cfb_ranks_fbs_teams_when_conferences_are_blank():
    from app.services.cfb_power_rankings import build_cfb_power_rankings

    rows = []
    teams = ("Alabama", "Georgia", "Ohio State", "Oregon", "Texas")
    day = 1
    for home_index, home in enumerate(teams):
        for away in teams[home_index + 1 :]:
            rows.append(
                {
                    "game_id": f"{home}-{away}",
                    "date": date(2025, 9, day),
                    "season": 2025,
                    "game_type": "regular",
                    "home_team": home,
                    "away_team": away,
                    "home_score": 31,
                    "away_score": 17,
                    "neutral_site": 1,
                    "home_conference": "",
                    "away_conference": "",
                }
            )
            day += 1
    rows.append(
        {
            "game_id": "cupcake",
            "date": date(2025, 9, 20),
            "season": 2025,
            "game_type": "regular",
            "home_team": "Alabama",
            "away_team": "Chattanooga",
            "home_score": 56,
            "away_score": 7,
            "neutral_site": 0,
            "home_conference": "",
            "away_conference": "",
        }
    )
    payload = build_cfb_power_rankings(as_of=date(2025, 10, 1), games=pd.DataFrame(rows))
    names = {team["team"] for team in payload["teams"]}
    assert names == set(teams)
    assert payload["team_count"] == 5
    assert "Chattanooga" not in names
    for team in payload["teams"]:
        counted = [point for point in team["subpoints"] if point["counted"]]
        assert round(sum(point["points"] for point in counted), 1) == team["power"]


def test_rankings_pages_are_on_the_sport_tabs():
    client = TestClient(app)
    nfl = client.get("/nfl/rankings")
    cfb = client.get("/cfb/rankings")
    assert nfl.status_code == 200
    assert "NFL power rankings" in nfl.text
    assert 'data-sport="nfl"' in nfl.text
    assert "power_rankings.js" in nfl.text
    assert cfb.status_code == 200
    assert "College football power rankings" in cfb.text
    assert 'data-sport="cfb"' in cfb.text
    slate = client.get("/nfl")
    cfb_slate = client.get("/cfb")
    assert "/static/app.js?v=20260922" in slate.text
    assert "/static/app.js?v=20260922" in cfb_slate.text
    script = client.get("/static/app.js")
    assert 'rankings: "/nfl/rankings"' in script.text
    assert 'rankings: "/cfb/rankings"' in script.text
    assert 'label: "Rankings"' in script.text
