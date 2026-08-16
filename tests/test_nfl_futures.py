"""NFL division futures — Wednesday cache and projected standings."""

from datetime import date

from app.ingest.nfl import NFL_DIVISIONS
from app.services.nfl_division_priors import (
    classify_division_race,
    prior_mix_weight,
    projected_wins_from_history,
)
from app.services.nfl_futures import (
    actual_records,
    build_nfl_futures,
    cache_is_current,
    project_from_probs,
    split_completed_remaining,
    wednesday_week_id,
)


def test_wednesday_week_id_rolls_on_wednesday():
    assert wednesday_week_id(date(2026, 8, 12)) == date(2026, 8, 12)
    assert wednesday_week_id(date(2026, 8, 15)) == date(2026, 8, 12)
    assert wednesday_week_id(date(2026, 8, 18)) == date(2026, 8, 12)
    assert wednesday_week_id(date(2026, 8, 19)) == date(2026, 8, 19)


def test_cache_is_current_requires_week_and_divisions():
    week = date(2026, 8, 12)
    payload = {
        "week_id": "2026-08-12",
        "season": 2026,
        "divisions": [{"key": "AFC_NORTH"}],
    }
    assert cache_is_current(payload, as_of=week, season=2026)
    assert not cache_is_current(payload, as_of=date(2026, 8, 19), season=2026)
    assert not cache_is_current({**payload, "divisions": []}, as_of=week, season=2026)


def _game(gid, home, away, *, home_win=None, week=1):
    return {
        "game_id": gid,
        "date": "2026-09-13",
        "season": 2026,
        "week": week,
        "home_team": home,
        "away_team": away,
        "home_team_abbr": home,
        "away_team_abbr": away,
        "divisional": 1,
        "neutral_site": 0,
        "completed": home_win is not None,
        "home_win": home_win,
        "tie": False,
    }


def test_split_and_records_lock_completed_games():
    games = [
        _game("1", "PIT", "BAL", home_win=1),
        _game("2", "CIN", "CLE", home_win=0),
        _game("3", "PIT", "CIN"),
    ]
    completed, remaining = split_completed_remaining(games)
    assert [g["game_id"] for g in completed] == ["1", "2"]
    assert [g["game_id"] for g in remaining] == ["3"]
    rec = actual_records(completed)
    assert rec["PIT"]["wins"] == 1
    assert rec["BAL"]["losses"] == 1
    assert rec["CLE"]["wins"] == 1
    assert rec["CIN"]["losses"] == 1


def test_project_from_probs_ranks_afc_north():
    remaining = [
        _game("r1", "PIT", "CLE", week=2),
        _game("r2", "BAL", "CIN", week=2),
        _game("r3", "PIT", "CIN", week=3),
        _game("r4", "BAL", "CLE", week=3),
    ]
    standings = project_from_probs(
        records={
            "PIT": {"wins": 1.0, "losses": 0.0, "ties": 0.0},
            "BAL": {"wins": 0.0, "losses": 1.0, "ties": 0.0},
            "CIN": {"wins": 0.0, "losses": 1.0, "ties": 0.0},
            "CLE": {"wins": 1.0, "losses": 0.0, "ties": 0.0},
        },
        remaining=remaining,
        probs={"r1": 0.85, "r2": 0.80, "r3": 0.82, "r4": 0.78},
        strength={abbr: 1500.0 for abbr in NFL_DIVISIONS},
        team_meta={
            "PIT": {"team": "Pittsburgh Steelers", "logo_url": "", "abbr": "PIT"},
            "BAL": {"team": "Baltimore Ravens", "logo_url": "", "abbr": "BAL"},
            "CIN": {"team": "Cincinnati Bengals", "logo_url": "", "abbr": "CIN"},
            "CLE": {"team": "Cleveland Browns", "logo_url": "", "abbr": "CLE"},
        },
        n_sims=200,
        seed=1,
    )
    north = standings["AFC_NORTH"]
    assert [row["abbr"] for row in north][0] == "PIT"
    assert north[0]["place_label"] == "1st"
    assert north[0]["division_win_pct"] > north[-1]["division_win_pct"]
    assert {row["abbr"] for row in north} == {"PIT", "BAL", "CIN", "CLE"}


def test_build_nfl_futures_uses_injected_schedule(tmp_path, monkeypatch):
    from app.services import nfl_futures as nf

    monkeypatch.setattr(nf, "FUTURES_JSON", tmp_path / "nfl_futures.json")
    games = []
    for i, (home, away) in enumerate(
        [
            ("PIT", "BAL"),
            ("CIN", "CLE"),
            ("BUF", "MIA"),
            ("NE", "NYJ"),
            ("HOU", "IND"),
            ("JAX", "TEN"),
            ("KC", "DEN"),
            ("LAC", "LV"),
            ("DAL", "NYG"),
            ("PHI", "WSH"),
            ("CHI", "DET"),
            ("GB", "MIN"),
            ("ATL", "CAR"),
            ("NO", "TB"),
            ("SF", "SEA"),
            ("LAR", "ARI"),
        ]
    ):
        games.append(_game(str(i + 1), home, away))
    payload = build_nfl_futures(
        season=2026,
        as_of=date(2026, 8, 15),
        refresh=True,
        n_sims=40,
        games=games,
        probs={str(i + 1): 0.6 for i in range(len(games))},
        strength={abbr: 1500.0 for abbr in NFL_DIVISIONS},
        write_cache=True,
    )
    assert payload["error"] is None
    assert payload["season"] == 2026
    assert payload["week_id"] == "2026-08-12"
    assert payload["games_remaining"] == 16
    assert payload["games_completed"] == 0
    assert len(payload["divisions"]) == 8
    names = [d["name"] for d in payload["divisions"]]
    assert names[0] == "AFC East"
    assert names[1] == "AFC North"
    north = next(d for d in payload["divisions"] if d["key"] == "AFC_NORTH")
    assert len(north["teams"]) == 4
    assert north["champion_abbr"] in {"PIT", "BAL", "CIN", "CLE"}
    assert north["race"] in {"clear", "lean", "toss_up"}
    assert "race_label" in north
    saved = nf.load_saved_nfl_futures()
    assert saved is not None
    assert cache_is_current(saved, as_of=date(2026, 8, 12), season=2026)


def test_prior_mix_is_full_before_week_one():
    assert prior_mix_weight(0, 272) == 1.0
    assert prior_mix_weight(136, 136) < 0.5
    assert prior_mix_weight(200, 72) == 0.25


def test_offseason_prior_keeps_recent_division_winner_ahead_of_last_place():
    import pandas as pd

    rows = []
    for season, wins in ((2025, 11), (2024, 5), (2023, 7)):
        for i in range(wins):
            rows.append(
                {
                    "game_id": f"{season}-chi-w{i}",
                    "date": f"{season}-09-10",
                    "season": season,
                    "week": i + 1,
                    "game_type": "regular",
                    "home_team_abbr": "CHI",
                    "away_team_abbr": "NYG",
                    "home_score": 24,
                    "away_score": 17,
                    "home_win": 1,
                    "divisional": 0,
                }
            )
        for i in range(17 - wins):
            rows.append(
                {
                    "game_id": f"{season}-chi-l{i}",
                    "date": f"{season}-11-10",
                    "season": season,
                    "week": wins + i + 1,
                    "game_type": "regular",
                    "home_team_abbr": "CHI",
                    "away_team_abbr": "NYG",
                    "home_score": 10,
                    "away_score": 27,
                    "home_win": 0,
                    "divisional": 0,
                }
            )
    for season, wins in ((2025, 9), (2024, 15), (2023, 12)):
        for i in range(wins):
            rows.append(
                {
                    "game_id": f"{season}-det-w{i}",
                    "date": f"{season}-09-11",
                    "season": season,
                    "week": i + 1,
                    "game_type": "regular",
                    "home_team_abbr": "DET",
                    "away_team_abbr": "NYG",
                    "home_score": 28,
                    "away_score": 14,
                    "home_win": 1,
                    "divisional": 0,
                }
            )
        for i in range(17 - wins):
            rows.append(
                {
                    "game_id": f"{season}-det-l{i}",
                    "date": f"{season}-11-11",
                    "season": season,
                    "week": wins + i + 1,
                    "game_type": "regular",
                    "home_team_abbr": "DET",
                    "away_team_abbr": "NYG",
                    "home_score": 13,
                    "away_score": 24,
                    "home_win": 0,
                    "divisional": 0,
                }
            )
    hist = pd.DataFrame(rows)
    prior = projected_wins_from_history(hist, 2026)
    assert prior["CHI"] > 8.3
    assert abs(prior["CHI"] - prior["DET"]) < 3.5


def test_close_division_is_tossup_not_a_pick():
    teams = [
        {"team": "Lions", "abbr": "DET", "expected_wins": 10.3, "division_win_pct": 0.49},
        {"team": "Vikings", "abbr": "MIN", "expected_wins": 9.5, "division_win_pct": 0.26},
        {"team": "Packers", "abbr": "GB", "expected_wins": 9.1, "division_win_pct": 0.17},
        {"team": "Bears", "abbr": "CHI", "expected_wins": 8.5, "division_win_pct": 0.09},
    ]
    assert classify_division_race(teams) == "toss_up"


def test_wide_gap_is_a_clear_favorite():
    teams = [
        {"team": "Eagles", "abbr": "PHI", "expected_wins": 11.6, "division_win_pct": 0.87},
        {"team": "Cowboys", "abbr": "DAL", "expected_wins": 8.1, "division_win_pct": 0.08},
        {"team": "Commanders", "abbr": "WSH", "expected_wins": 7.7, "division_win_pct": 0.05},
        {"team": "Giants", "abbr": "NYG", "expected_wins": 5.6, "division_win_pct": 0.00},
    ]
    assert classify_division_race(teams) == "clear"
