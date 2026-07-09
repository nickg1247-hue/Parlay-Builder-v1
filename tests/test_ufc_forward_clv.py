"""UFC forward CLV logging tests."""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from app.services import ufc_forward_clv as ufc_clv


@pytest.fixture
def isolated_ufc_clv_log(tmp_path, monkeypatch):
    log_path = tmp_path / "forward_clv_ufc_log.jsonl"
    monkeypatch.setattr(ufc_clv, "FORWARD_CLV_UFC_LOG", log_path)
    return log_path


def test_pick_id_format():
    assert ufc_clv.pick_id("2024-01-13", "401623977", "home") == (
        "ufc:2024-01-13:401623977:home"
    )


def test_log_live_picks_writes_plus_ev(isolated_ufc_clv_log, monkeypatch):
    monkeypatch.setattr(ufc_clv, "FORWARD_CLV_UFC_LOG", isolated_ufc_clv_log)
    payload = {
        "mode": "live",
        "odds_source": "the_odds_api_live",
        "date": "2024-01-13",
        "edge_threshold": 0.08,
        "active_moneyline_model": {"model_version": "v1_logistic_platt"},
        "slate": [
            {
                "fight_id": "401623977",
                "game_id": "401623977",
                "home_team": "Fighter A",
                "away_team": "Fighter B",
                "matchup": "Fighter A vs Fighter B",
                "model_prob_home": 0.62,
                "market_prob_home": 0.5,
                "plus_ev_single": True,
                "best_pick": {
                    "side": "home",
                    "fighter": "Fighter A",
                    "edge": 0.12,
                    "american_odds": -110,
                },
            }
        ],
    }
    written = ufc_clv.log_live_picks(payload)
    assert len(written) == 1
    assert written[0]["sport"] == "ufc"
    assert written[0]["fighter"] == "Fighter A"


def test_log_live_picks_skips_demo(isolated_ufc_clv_log, monkeypatch):
    monkeypatch.setattr(ufc_clv, "FORWARD_CLV_UFC_LOG", isolated_ufc_clv_log)
    payload = {
        "mode": "demo",
        "odds_source": "the_odds_api_live",
        "date": "2024-01-13",
        "slate": [],
    }
    assert ufc_clv.log_live_picks(payload) == []


def test_summarize_clv_empty(isolated_ufc_clv_log, monkeypatch):
    monkeypatch.setattr(ufc_clv, "FORWARD_CLV_UFC_LOG", isolated_ufc_clv_log)
    summary = ufc_clv.summarize_clv(days=30)
    assert summary["sport"] == "ufc"
    assert summary["picks_logged"] == 0
    assert summary["picks_settled"] == 0
    assert summary["hit_rate"] is None


def test_grade_pick_results(isolated_ufc_clv_log, monkeypatch):
    monkeypatch.setattr(ufc_clv, "FORWARD_CLV_UFC_LOG", isolated_ufc_clv_log)
    row = {
        "sport": "ufc",
        "pick_id": "ufc:2024-01-13:401623977:home",
        "board_date": "2024-01-13",
        "fight_id": "401623977",
        "game_id": "401623977",
        "side": "home",
        "home_team": "Magomed Ankalaev",
        "away_team": "Johnny Walker",
        "pick_won": None,
    }
    isolated_ufc_clv_log.write_text(json.dumps(row) + "\n", encoding="utf-8")

    fights = pd.DataFrame(
        [{"fight_id": "401623977", "home_win": 1, "date": "2024-01-13", "season": 2024,
          "home_team": "A", "away_team": "B", "event_id": "1", "event_name": "T",
          "weight_class": "", "card_segment": "", "home_rest_days": 90, "away_rest_days": 90,
          "home_b2b": 0, "away_b2b": 0}]
    )
    with patch("app.services.ufc_forward_clv.load_fights", return_value=fights):
        result = ufc_clv.grade_pick_results()
    assert result["graded"] == 1
    summary = ufc_clv.summarize_clv(days=3650)
    assert summary["picks_settled"] == 1
    assert summary["hit_rate"] == 1.0
