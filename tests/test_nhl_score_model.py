"""NHL Poisson score model — winner, puck line, and total."""

from app.models.nhl_score_model import predict_matchup, score_grid
from app.services.nhl_ratings import canonical_abbr, team_abbr_from_name
from app.services.scores_nhl import live_game_record


def _rating(gf: float, ga: float, games: int = 82) -> dict:
    return {"gf_per_game": gf, "ga_per_game": ga, "games": games, "name": "Club"}


def test_favorite_wins_more_often_than_the_underdog():
    strong = predict_matchup(
        home_team="Home",
        away_team="Away",
        home_abbr="HOM",
        away_abbr="AWY",
        home_rating=_rating(3.6, 2.5),
        away_rating=_rating(2.4, 3.4),
        league_gf=3.0,
        home_spread=-1.5,
        ou_line=6.5,
    )
    weak = predict_matchup(
        home_team="Home",
        away_team="Away",
        home_abbr="HOM",
        away_abbr="AWY",
        home_rating=_rating(2.4, 3.4),
        away_rating=_rating(3.6, 2.5),
        league_gf=3.0,
        home_spread=1.5,
        ou_line=6.5,
    )
    assert strong["model_pick_side"] == "home"
    assert strong["model_prob_home"] > weak["model_prob_home"]
    assert strong["model_prob_home"] > 0.55


def test_puckline_cover_is_stricter_than_the_moneyline():
    pred = predict_matchup(
        home_team="Capitals",
        away_team="Penguins",
        home_abbr="WSH",
        away_abbr="PIT",
        home_rating=_rating(3.4, 2.6),
        away_rating=_rating(2.8, 3.1),
        league_gf=3.0,
        home_spread=-1.5,
        ou_line=6.5,
    )
    assert pred["model_prob_home_cover"] < pred["model_prob_home"]
    assert pred["spread_pick"]
    assert pred["totals_pick"].startswith("Over") or pred["totals_pick"].startswith("Under")
    assert abs(pred["model_prob_over"] + pred["model_prob_under"] - 1) < 0.02


def test_higher_expected_goals_raise_the_over():
    low = score_grid(2.2, 2.1)
    high = score_grid(3.6, 3.5)
    assert high["expected_final_total"] > low["expected_final_total"]
    assert 0.99 < low["prob_home_win"] + low["prob_away_win"] < 1.01


def test_team_names_map_to_espn_abbreviations():
    assert team_abbr_from_name("Montréal Canadiens") == "MTL"
    assert team_abbr_from_name("Utah Mammoth") == "UTA"
    assert team_abbr_from_name("St. Louis Blues") == "STL"
    assert canonical_abbr("LA") == "LAK"
    assert canonical_abbr("TB") == "TBL"


def test_espn_event_parses_moneyline_spread_and_total():
    event = {
        "id": "401",
        "date": "2026-10-07T23:30Z",
        "season": {"year": 2027, "type": 2},
        "competitions": [
            {
                "neutralSite": False,
                "status": {"type": {"state": "pre", "description": "Scheduled"}},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "0",
                        "team": {"id": "23", "abbreviation": "WSH", "displayName": "Washington Capitals", "logo": "https://example/wsh.png"},
                    },
                    {
                        "homeAway": "away",
                        "score": "0",
                        "team": {"id": "16", "abbreviation": "TB", "displayName": "Tampa Bay Lightning"},
                    },
                ],
                "odds": [
                    {
                        "spread": -1.5,
                        "overUnder": 6.5,
                        "moneyline": {
                            "home": {"close": {"odds": "-170"}},
                            "away": {"close": {"odds": "+142"}},
                        },
                    }
                ],
            }
        ],
    }
    game = live_game_record(event)
    assert game["sport"] == "nhl"
    assert game["home_team_abbr"] == "WSH"
    assert game["away_team_abbr"] == "TBL"
    assert game["home_ml"] == -170
    assert game["away_ml"] == 142
    assert game["home_spread_point"] == -1.5
    assert game["ou_line"] == 6.5
    assert game["game_type"] == "regular"
