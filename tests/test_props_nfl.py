"""NFL player props — parse posted lines, both sides, NFL-specific scoring."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import Mock
import sys
import types

import pytest

from app.services.prop_books import normalize_prop_sport
from app.services.prop_engine.nfl_markets import MARKET_LABELS, list_nfl_market_types
from app.services.prop_engine.nfl_context import (
    cash_confidence,
    hit_rates_from_log,
    hit_rates_vs_line,
    kickoff_profile,
    weather_market_multiplier,
    weather_profile,
)
from app.services.prop_engine.nfl_projections import build_nfl_projection, score_nfl_prop
from app.services.props_nfl import _load_nfl_props_schedule, parse_nfl_event_props, score_nfl_prop_row


def _event(over: int = -110, under: int = -110, point: float = 74.5) -> dict:
    return {
        "id": "evt1",
        "home_team": "Cleveland Browns",
        "away_team": "Pittsburgh Steelers",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_rush_yds",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Nick Chubb",
                                "point": point,
                                "price": over,
                            },
                            {
                                "name": "Under",
                                "description": "Nick Chubb",
                                "point": point,
                                "price": under,
                            },
                        ],
                    },
                    {
                        "key": "player_anytime_td",
                        "outcomes": [
                            {"name": "Yes", "description": "Nick Chubb", "price": -140},
                            {"name": "No", "description": "Nick Chubb", "price": 110},
                        ],
                    },
                ],
            }
        ],
    }


def _stub_schedule_nfl(monkeypatch, *, resolve=None, get_schedule=None):
    stub = types.ModuleType("app.services.schedule_nfl")
    stub.resolve_nfl_slate_date = resolve or Mock()
    stub.get_nfl_schedule = get_schedule or Mock(return_value={"date": date.today().isoformat(), "games": []})
    monkeypatch.setitem(sys.modules, "app.services.schedule_nfl", stub)
    return stub


def test_today_props_schedule_looks_ahead_when_no_games(monkeypatch):
    today = date.today()
    sunday = today + timedelta(days=4)
    payload = {"date": sunday.isoformat(), "games": [{"game_id": "nfl-1"}]}
    stub = _stub_schedule_nfl(
        monkeypatch,
        resolve=Mock(return_value=(sunday, 4)),
        get_schedule=Mock(return_value=payload),
    )
    resolved, schedule = _load_nfl_props_schedule(today)
    assert resolved == sunday
    assert schedule["games"][0]["game_id"] == "nfl-1"
    stub.resolve_nfl_slate_date.assert_called_once_with(today)
    stub.get_nfl_schedule.assert_called_once_with(sunday)


def test_historical_props_schedule_does_not_look_ahead(monkeypatch):
    past = date.today() - timedelta(days=14)
    payload = {"date": past.isoformat(), "games": []}
    stub = _stub_schedule_nfl(
        monkeypatch,
        resolve=Mock(),
        get_schedule=Mock(return_value=payload),
    )
    resolved, _schedule = _load_nfl_props_schedule(past)
    assert resolved == past
    stub.resolve_nfl_slate_date.assert_not_called()
    stub.get_nfl_schedule.assert_called_once_with(past)


def test_et_today_looks_ahead_even_when_not_utc_today(monkeypatch):
    et_today = date(2026, 8, 28)
    sunday = date(2026, 8, 30)
    payload = {"date": sunday.isoformat(), "games": [{"game_id": "nfl-1"}]}
    monkeypatch.setattr("app.services.props_nfl.slate_today", lambda: et_today)
    stub = _stub_schedule_nfl(
        monkeypatch,
        resolve=Mock(return_value=(sunday, 2)),
        get_schedule=Mock(return_value=payload),
    )
    resolved, schedule = _load_nfl_props_schedule(et_today)
    assert resolved == sunday
    assert schedule["games"][0]["game_id"] == "nfl-1"
    stub.resolve_nfl_slate_date.assert_called_once_with(et_today)


def test_empty_reason_copy_distinguishes_quota_and_cache():
    from app.services.props_nfl import _empty_message

    quota = _empty_message("quota", games_on_slate=10, games_with_props=2)
    assert "quota" in quota.lower()
    assert "2/10" in quota
    no_cache = _empty_message("no_cache", games_on_slate=8, games_with_props=0)
    assert "not cached" in no_cache.lower()
    assert "Refresh" in no_cache
    kickoff = _empty_message("kickoff")
    assert "kickoff" in kickoff.lower()
    no_slate = _empty_message("no_slate")
    assert "No NFL games" in no_slate


def test_game_props_cache_miss_does_not_fetch(monkeypatch):
    from app.services.props_nfl import build_nfl_game_props

    monkeypatch.setattr(
        "app.services.schedule_nfl.get_nfl_game",
        lambda gid, d: {
            "game": {
                "game_id": gid,
                "status": "Preview",
                "home_team": "Cleveland Browns",
                "away_team": "Pittsburgh Steelers",
                "home_team_abbr": "CLE",
                "away_team_abbr": "PIT",
            }
        },
    )

    def boom(*_a, **_k):
        raise AssertionError("Odds API must not run on cache-only game props")

    monkeypatch.setattr("app.services.props_nfl.fetch_nfl_event_props_if_allowed", boom)
    monkeypatch.setattr("app.services.props_nfl._load_events", boom)
    out = build_nfl_game_props("nfl-cache-miss", date(2026, 8, 28), refresh=False)
    assert out["empty_reason"] == "no_cache"
    assert out["props"] == []


def test_normalize_prop_sport():
    assert normalize_prop_sport(None) == "mlb"
    assert normalize_prop_sport("NFL") == "nfl"
    assert normalize_prop_sport("mlb") == "mlb"


def test_markets_are_sport_specific():
    nfl = {m["key"] for m in list_nfl_market_types()}
    assert "player_rush_yds" in nfl
    assert "player_anytime_td" in nfl
    assert "batter_hits" not in nfl
    assert "batter_hits" not in MARKET_LABELS


def test_parse_nfl_event_includes_over_under_and_anytime_td():
    rows = parse_nfl_event_props(_event(), "draftkings")
    by_market = {r["market_type"]: r for r in rows}
    rush = by_market["player_rush_yds"]
    assert rush["line"] == 74.5
    assert rush["over_odds"] == -110
    assert rush["under_odds"] == -110
    assert rush["complete_market"] is True
    td = by_market["player_anytime_td"]
    assert td["line"] == 0.5
    assert td["over_odds"] == -140
    assert td["under_odds"] == 110


def test_nfl_projection_weights_recent_role_over_season():
    # Season-like 40, last 3 around 80 — projection must move toward recent.
    values = [40, 38, 42, 41, 39, 78, 81, 79]
    out = build_nfl_projection(values, market_type="player_rush_yds")
    assert out["model_projection"] is not None
    assert out["l3_avg"] > 70
    assert out["season_avg"] < 60
    assert out["model_projection"] > out["season_avg"]
    assert out["role_shift"] is not None and out["role_shift"] > 0.3


def test_nfl_score_requires_edge_and_sample():
    weak = score_nfl_prop(
        edge=0.01,
        sample_games=1,
        role_shift=None,
        projection_confidence="low",
        injury_note=None,
    )
    strong = score_nfl_prop(
        edge=0.09,
        sample_games=6,
        role_shift=0.1,
        projection_confidence="high",
        injury_note=None,
    )
    assert weak["actionable"] is False
    assert strong["actionable"] is True
    assert strong["prop_score"] > weak["prop_score"]
    assert strong["line_strength"] in ("very_strong", "elite", "strong")


def test_search_props_dispatches_nfl_without_touching_mlb(monkeypatch):
    sklearn = pytest.importorskip("sklearn")
    del sklearn
    from app.services.props_platform import search_props

    called = {}

    def fake_nfl(*args, **kwargs):
        called["nfl"] = kwargs
        return {"props": [{"player": "A", "sport": "nfl"}], "total_matched": 1}

    def fake_mlb(*args, **kwargs):
        called["mlb"] = True
        return {"props": [{"player": "B"}], "total_matched": 1}

    monkeypatch.setattr("app.services.props_platform.search_nfl_daily_props", fake_nfl)
    monkeypatch.setattr("app.services.props_platform.search_mlb_daily_props", fake_mlb)
    result = search_props("nfl", position="WR", min_hit_l10=0.9)
    assert result["sport"] == "nfl"
    assert result["props"][0]["player"] == "A"
    assert "mlb" not in called
    assert called["nfl"]["position"] == "WR"


def test_hit_rates_l5_bleeds_prior_season_season_stays_empty():
    entries = [
        {"date": "2025-12-28", "season_year": 2025, "opponent": "PIT", "stat_value": 90},
        {"date": "2025-12-21", "season_year": 2025, "opponent": "CIN", "stat_value": 88},
        {"date": "2025-12-14", "season_year": 2025, "opponent": "PIT", "stat_value": 85},
        {"date": "2025-12-07", "season_year": 2025, "opponent": "BAL", "stat_value": 95},
        {"date": "2025-11-30", "season_year": 2025, "opponent": "DEN", "stat_value": 80},
        {"date": "2025-11-23", "season_year": 2025, "opponent": "LV", "stat_value": 70},
    ]
    # Chronological oldest-first for windows.
    entries = sorted(entries, key=lambda e: e["date"])
    rates = hit_rates_from_log(
        entries,
        64.5,
        slate_date=date(2026, 9, 13),
        opponent="PIT",
        similar_opponents={"PIT", "BAL", "CIN", "CLE"},
    )
    assert rates["sample_games_season"] == 0
    assert rates["hit_rate_over_season"] is None
    assert rates["hit_rate_over_l5"] == 1.0
    assert rates["sample_games_l5"] == 5
    assert rates["sample_games_vs_opp"] == 2
    assert rates["hit_window"] == "prior_season"


def test_cash_confidence_uses_hit_rates_when_projection_missing():
    out = cash_confidence(
        model_p=None,
        hit_l5=0.80,
        hit_l10=0.70,
        hit_vs_opp=0.75,
        sample_games=10,
        injury_note=None,
        weather_risk="none",
        projection_confidence="medium",
    )
    assert out["confidence_pct"] is not None
    assert out["confidence_pct"] > 50


def test_hit_rates_vs_posted_line():
    values = [40, 50, 60, 70, 75, 80, 90, 100, 110, 120]
    rates = hit_rates_vs_line(values, 75.5)
    assert rates["hit_rate_over_l5"] == 1.0
    assert rates["hit_rate_under_l5"] == 0.0
    assert rates["hit_rate_over_season"] == 0.5
    assert rates["sample_games_season"] == 10


def test_cash_confidence_haircuts_injury_and_weather():
    healthy = cash_confidence(
        model_p=0.62,
        hit_l10=0.70,
        sample_games=8,
        injury_note=None,
        weather_risk="none",
        projection_confidence="high",
    )
    hurt = cash_confidence(
        model_p=0.62,
        hit_l10=0.70,
        sample_games=8,
        injury_note="Questionable — ankle",
        weather_risk="high",
        projection_confidence="high",
    )
    assert healthy["confidence_pct"] is not None
    assert hurt["confidence_pct"] < healthy["confidence_pct"]


def test_kickoff_and_weather_multipliers():
    profile = kickoff_profile("2026-09-13T20:20:00Z")
    assert profile["window"] in ("sunday_night", "primetime", "afternoon", "sunday_early")
    wx = weather_profile(
        {
            "indoor": False,
            "weather_display": "Rain",
            "weather_temp": 48,
            "weather_wind_mph": 18,
        }
    )
    assert wx["risk"] == "high"
    passing = weather_market_multiplier("player_pass_yds", wx)
    rushing = weather_market_multiplier("player_rush_yds", wx)
    assert passing < 1.0
    assert rushing > 1.0
    indoor = weather_profile({"indoor": True, "weather_display": "Rain"})
    assert indoor["risk"] == "none"
    assert weather_market_multiplier("player_pass_yds", indoor) == 1.0


def test_hit_rates_count_misses_not_perfect_unders():
    """Etienne-style: two overs in last 5 must not show as 5/5 unders."""
    entries = [
        {"date": "2025-11-23", "season_year": 2025, "opponent": "ARI", "stat_value": 86, "stats": {"rushingAttempts": 18}},
        {"date": "2025-11-30", "season_year": 2025, "opponent": "TEN", "stat_value": 28, "stats": {"rushingAttempts": 12}},
        {"date": "2025-12-07", "season_year": 2025, "opponent": "IND", "stat_value": 74, "stats": {"rushingAttempts": 20}},
        {"date": "2025-12-14", "season_year": 2025, "opponent": "NYJ", "stat_value": 32, "stats": {"rushingAttempts": 12}},
        {"date": "2025-12-21", "season_year": 2025, "opponent": "DEN", "stat_value": 50, "stats": {"rushingAttempts": 16}},
        {"date": "2025-12-28", "season_year": 2025, "opponent": "IND", "stat_value": 76, "stats": {"rushingAttempts": 19}},
        {"date": "2026-01-04", "season_year": 2025, "opponent": "TEN", "stat_value": 32, "stats": {"rushingAttempts": 14}},
        {"date": "2026-01-11", "season_year": 2025, "opponent": "BUF", "stat_value": 67, "stats": {"rushingAttempts": 10}},
        {"date": "2026-08-15", "season_year": 2026, "opponent": "KC", "stat_value": 0, "stats": {"rushingAttempts": 0}},
        {"date": "2026-08-22", "season_year": 2026, "opponent": "MIA", "stat_value": 0, "stats": {"rushingAttempts": 0}},
        {"date": "2026-08-29", "season_year": 2026, "opponent": "TB", "stat_value": 0, "stats": {"rushingAttempts": 0}},
    ]
    rates = hit_rates_from_log(
        entries,
        55.5,
        slate_date=date(2026, 9, 13),
        opponent="CAR",
        market_stat="rushingYards",
    )
    assert rates["hit_count_under_l5"] == 3
    assert rates["hit_count_over_l5"] == 2
    assert rates["sample_games_l5"] == 5
    assert rates["hit_rate_under_l5"] == 0.6
    assert rates["hit_rate_under_l10"] != 1.0
    recent5 = [g["stat"] for g in rates["recent_games"][-5:]]
    assert recent5 == [32.0, 50.0, 76.0, 32.0, 67.0]


def test_score_nfl_prop_row_attaches_hit_rates_and_confidence(monkeypatch):
    monkeypatch.setattr(
        "app.services.props_nfl.resolve_nfl_player",
        lambda name, team: {"position": "RB", "athlete_id": "123"},
    )
    monkeypatch.setattr("app.services.props_nfl.player_injury_note", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.props_nfl.nfl_game_log_entries",
        lambda *_a, **_k: [
            {"date": f"2025-11-{i:02d}", "season_year": 2025, "opponent": "PIT", "stat_value": v}
            for i, v in enumerate([40, 45, 50, 80, 82, 85, 88, 90], start=10)
        ],
    )
    monkeypatch.setattr("app.services.props_nfl.opponent_defense_profile", lambda *_a, **_k: None)
    rows = score_nfl_prop_row(
        {
            "player": "Nick Chubb",
            "market_type": "player_rush_yds",
            "market_label": "Rushing yards",
            "line": 64.5,
            "line_kind": "main",
            "over_odds": -110,
            "under_odds": -110,
        },
        game={
            "game_id": "401",
            "home_team_abbr": "CLE",
            "away_team_abbr": "PIT",
            "start_time_utc": "2026-09-13T17:00:00Z",
            "indoor": True,
        },
        game_date=date(2026, 9, 13),
        env={"home": "CLE", "away": "PIT", "spread_home": -3.5, "total": 41.5},
    )
    assert len(rows) == 1
    prop = rows[0]
    assert prop["hit_rate_over_l5"] is not None
    assert prop["hit_rate_l10"] is not None
    assert prop["confidence_pct"] is not None
    assert prop["line_kind"] == "main"
    assert "sides" in prop
