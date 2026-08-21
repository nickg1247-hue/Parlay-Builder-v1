"""NFL player props — parse posted lines, both sides, NFL-specific scoring."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import Mock
import sys
import types

import pytest

from app.services.prop_books import normalize_prop_sport
from app.services.prop_engine.nfl_markets import MARKET_LABELS, list_nfl_market_types
from app.services.prop_engine.nfl_projections import build_nfl_projection, score_nfl_prop
from app.services.props_nfl import _load_nfl_props_schedule, parse_nfl_event_props


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
