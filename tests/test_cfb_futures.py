"""CFB conference placement + playoff futures."""

from datetime import date

from fastapi.testclient import TestClient

from app.ingest.cfb_season_schedule import parse_schedule_row
from app.main import app
from app.services.cfb_futures import (
    assign_team_conferences,
    build_cfb_futures,
    match_conference,
    project_from_probs,
    split_completed_remaining,
    sunday_week_id,
)

client = TestClient(app)


def test_sunday_week_id_resets_on_sunday():
    assert sunday_week_id(date(2026, 8, 16)) == date(2026, 8, 16)
    assert sunday_week_id(date(2026, 8, 19)) == date(2026, 8, 16)
    assert sunday_week_id(date(2026, 8, 22)) == date(2026, 8, 16)
    assert sunday_week_id(date(2026, 8, 23)) == date(2026, 8, 23)


def test_match_power_and_group_conferences():
    assert match_conference("Big Ten")["key"] == "big_ten"
    assert match_conference("SEC")["tier"] == "power"
    assert match_conference("Mid-American")["key"] == "mac"
    assert match_conference("American Athletic")["key"] == "aac"
    assert match_conference("FBS Independents") is None


def test_parse_unplayed_schedule_row():
    row = parse_schedule_row(
        {
            "id": 401234,
            "startDate": "2026-08-29T16:00:00.000Z",
            "week": 1,
            "homeTeam": "Ohio State",
            "awayTeam": "Texas",
            "homeConference": "Big Ten",
            "awayConference": "Big 12",
            "homeClassification": "fbs",
            "awayClassification": "fbs",
            "conferenceGame": False,
            "neutralSite": True,
            "completed": False,
        },
        2026,
    )
    assert row is not None
    assert row["completed"] is False
    assert row["home_win"] is None
    assert row["home_team"] == "Ohio State"


def test_split_locks_saturday_results_before_sunday():
    games = [
        {
            "date": "2026-08-29",
            "home_win": 1,
            "game_id": "1",
        },
        {
            "date": "2026-08-30",
            "home_win": None,
            "game_id": "2",
        },
    ]
    done, left = split_completed_remaining(games, as_of=date(2026, 8, 30))
    assert [g["game_id"] for g in done] == ["1"]
    assert [g["game_id"] for g in left] == ["2"]


def _game(gid, home, away, conf, p=None, completed=False, home_win=None, week=4):
    row = {
        "game_id": gid,
        "date": "2026-09-19",
        "season": 2026,
        "week": week,
        "home_team": home,
        "away_team": away,
        "home_conference": conf,
        "away_conference": conf,
        "conference_game": 1,
        "neutral_site": 0,
        "completed": completed,
        "home_win": home_win,
        "title_game": False,
    }
    return row


def test_projected_order_follows_win_probability():
    teams = ["Ohio State", "Oregon", "Indiana", "Purdue"]
    games = []
    probs = {}
    gid = 1
    # Round-robin: favorite always home and heavily favored in its own games
    favorites = {
        "Ohio State": 0.92,
        "Oregon": 0.78,
        "Indiana": 0.62,
        "Purdue": 0.40,
    }
    for i, home in enumerate(teams):
        for away in teams[i + 1 :]:
            game = _game(str(gid), home, away, "Big Ten")
            games.append(game)
            # home favorite if higher listed strength
            p_home = 0.85 if favorites[home] > favorites[away] else 0.20
            probs[str(gid)] = p_home
            gid += 1

    team_conf = assign_team_conferences(games)
    assert team_conf["Ohio State"] == "big_ten"
    strength = {team: 1800 - 50 * i for i, team in enumerate(teams)}
    out = project_from_probs(
        team_conf=team_conf,
        records={},
        remaining=games,
        probs=probs,
        strength=strength,
        n_sims=200,
        seed=20260816,
        season_progress=1.0,
    )
    order = [row["team"] for row in out["conferences"]["big_ten"]]
    assert order[0] == "Ohio State"
    assert order[-1] == "Purdue"
    assert order.index("Oregon") < order.index("Indiana")
    assert out["conferences"]["big_ten"][0]["title_pct"] > 0.5


def test_playoff_has_twelve_seeds_and_first_round():
    conferences = [
        ("SEC", [f"SEC{i}" for i in range(4)]),
        ("Big Ten", [f"B1G{i}" for i in range(4)]),
        ("Big 12", [f"B12{i}" for i in range(4)]),
        ("ACC", [f"ACC{i}" for i in range(4)]),
        ("Mid-American", [f"MAC{i}" for i in range(4)]),
    ]
    games = []
    probs = {}
    strength = {}
    gid = 1
    for conf, teams in conferences:
        for i, team in enumerate(teams):
            strength[team] = 1700 - i * 20
        for i in range(0, len(teams), 2):
            game = _game(str(gid), teams[i], teams[i + 1], conf)
            games.append(game)
            probs[str(gid)] = 0.80
            gid += 1
    team_conf = assign_team_conferences(games)
    out = project_from_probs(
        team_conf=team_conf,
        records={},
        remaining=games,
        probs=probs,
        strength=strength,
        n_sims=80,
        seed=1,
    )
    seeds = out["playoff"]["seeds"]
    assert len(seeds) == 12
    assert seeds[0]["bye"] is True
    assert seeds[4]["bye"] is False
    assert len(out["playoff"]["first_round"]) == 4
    assert {row["seed"] for row in seeds} == set(range(1, 13))
    assert len(out["overall"]) == 20
    assert abs(sum(row["national_title_pct"] for row in out["overall"]) - 1.0) < 0.01
    for row in out["overall"]:
        assert row["national_title_pct"] <= row["final_pct"]
        assert row["final_pct"] <= row["semifinal_pct"]
        assert row["semifinal_pct"] <= row["quarterfinal_pct"]
        assert row["quarterfinal_pct"] <= row["playoff_pct"]
        assert row["likely_record"]
        assert row["win_range_low"] <= row["likely_wins"] <= row["win_range_high"]
        assert abs(sum(bucket["pct"] for bucket in row["win_distribution"]) - 1.0) < 0.01


def test_build_cfb_futures_uses_injected_schedule(tmp_path, monkeypatch):
    from app.services import cfb_futures as mod

    monkeypatch.setattr(mod, "FUTURES_JSON", tmp_path / "cfb_futures.json")
    games = [
        _game("10", "Ohio State", "Purdue", "Big Ten"),
        _game("11", "Toledo", "Akron", "Mid-American"),
    ]
    payload = build_cfb_futures(
        season=2026,
        as_of=date(2026, 8, 16),
        refresh=True,
        n_sims=40,
        games=games,
        probs={"10": 0.9, "11": 0.7},
        strength={"Ohio State": 1800, "Purdue": 1400, "Toledo": 1500, "Akron": 1300},
        write_cache=True,
    )
    names = {c["name"] for c in payload["conferences"]}
    assert "Big Ten" in names
    assert "MAC" in names
    assert payload["week_id"] == "2026-08-16"
    assert payload["error"] is None
    cached = build_cfb_futures(season=2026, as_of=date(2026, 8, 19), refresh=False)
    assert cached["generated_at"] == payload["generated_at"]


def test_futures_page_and_api_empty_cache(tmp_path, monkeypatch):
    from app.services import cfb_futures as mod

    monkeypatch.setattr(mod, "FUTURES_JSON", tmp_path / "missing.json")
    monkeypatch.setattr(
        mod,
        "build_cfb_futures",
        lambda **kwargs: {
            "sport": "cfb",
            "season": 2026,
            "week_id": "2026-08-16",
            "conferences": [],
            "playoff": {"seeds": [], "first_round": []},
            "error": None,
        },
    )
    page = client.get("/cfb/futures")
    assert page.status_code == 200
    assert "cfb_futures.js" in page.text
    resp = client.get("/api/cfb/futures")
    assert resp.status_code == 200
    assert resp.json()["sport"] == "cfb"
