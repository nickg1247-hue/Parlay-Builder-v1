"""Forward CLV logging and summary tests."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import forward_clv as fc
from app.services.daily_board import build_daily_board, DAILY_BOARD_CACHE

client = TestClient(app)

SAMPLE_SLATE_GAME = {
    "game_id": "777001",
    "matchup": "Boston Red Sox @ New York Yankees",
    "away_team": "Boston Red Sox",
    "home_team": "New York Yankees",
    "model_prob_home": 0.58,
    "market_prob_home": 0.50,
    "plus_ev_single": True,
    "best_pick": {
        "side": "home",
        "team": "New York Yankees",
        "edge": 0.10,
        "american_odds": 105,
    },
}


@pytest.fixture
def isolated_clv_log(tmp_path, monkeypatch):
    log_path = tmp_path / "forward_clv_log.jsonl"
    monkeypatch.setattr(fc, "FORWARD_CLV_LOG", log_path)
    return log_path


def test_clv_implied_prob_positive_when_beat_close():
    # Pick home +105 (~48.8% vig-free lower than -110 ~52.4%) — use market probs directly
    pick_prob = 0.45
    close_prob = 0.52
    assert fc.clv_implied_prob(pick_prob, close_prob) == pytest.approx(0.07)


def test_clv_implied_prob_negative_when_worse_than_close():
    pick_prob = 0.60
    close_prob = 0.55
    assert fc.clv_implied_prob(pick_prob, close_prob) < 0


def test_clv_decimal_ratio_better_price_at_pick():
    assert fc.clv_decimal_ratio(200, 180) > 0
    # Worse price at pick than close (locked -150, close -130)
    assert fc.clv_decimal_ratio(-150, -130) < 0


def test_log_live_picks_writes_plus_ev_singles(isolated_clv_log):
    payload = {
        "mode": "live",
        "odds_source": "the_odds_api",
        "date": "2026-06-10",
        "edge_threshold": 0.08,
        "active_moneyline_model": {"model_version": "v3_logistic_pruned_platt"},
        "slate": [SAMPLE_SLATE_GAME],
    }
    written = fc.log_live_picks(payload)
    assert len(written) == 1
    assert written[0]["pick_id"] == "2026-06-10:777001:home"
    assert written[0]["american_odds_at_pick"] == 105
    rows = fc._read_all_rows()
    assert len(rows) == 1


def test_log_live_picks_skips_demo_and_cache_sources(isolated_clv_log):
    payload = {
        "mode": "demo",
        "odds_source": "historical_cache",
        "date": "2025-08-15",
        "slate": [SAMPLE_SLATE_GAME],
    }
    assert fc.log_live_picks(payload) == []
    payload["mode"] = "live"
    payload["odds_source"] = "none"
    assert fc.log_live_picks(payload) == []
    assert fc._read_all_rows() == []


def test_log_idempotent_within_five_american_points(isolated_clv_log):
    payload = {
        "mode": "live",
        "odds_source": "the_odds_api",
        "date": "2026-06-10",
        "edge_threshold": 0.08,
        "active_moneyline_model": {},
        "slate": [SAMPLE_SLATE_GAME],
    }
    fc.log_live_picks(payload)
    game2 = {**SAMPLE_SLATE_GAME, "best_pick": {**SAMPLE_SLATE_GAME["best_pick"], "american_odds": 108}}
    payload["slate"] = [game2]
    fc.log_live_picks(payload)
    assert len(fc._read_all_rows()) == 1

    game3 = {**SAMPLE_SLATE_GAME, "best_pick": {**SAMPLE_SLATE_GAME["best_pick"], "american_odds": 115}}
    payload["slate"] = [game3]
    fc.log_live_picks(payload)
    assert len(fc._read_all_rows()) == 2


def test_backfill_sets_close_fields(isolated_clv_log, monkeypatch):
    fc._append_row(
        {
            "pick_id": "2026-06-10:777001:home",
            "board_date": "2026-06-10",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "side": "home",
            "american_odds_at_pick": 105,
            "market_prob_at_pick": 0.45,
            "game_id": "777001",
        }
    )
    events = [
        {
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "commence_time": "2026-06-10T23:00:00Z",
            "bookmakers": [
                {
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "New York Yankees", "price": -110},
                                {"name": "Boston Red Sox", "price": 100},
                            ],
                        }
                    ]
                }
            ],
        }
    ]
    monkeypatch.setattr(fc, "fetch_mlb_moneylines", lambda: events)
    monkeypatch.setattr(fc, "load_games", lambda: pd.DataFrame())
    result = fc.backfill_closing_odds(date(2026, 6, 10))
    assert result["updated"] == 1
    latest = fc._latest_by_pick_id(fc._read_all_rows())["2026-06-10:777001:home"]
    assert latest["close_american_odds"] == -110
    assert latest["clv_implied_prob"] is not None


def test_api_clv_summary():
    response = client.get("/api/clv/summary?days=30")
    assert response.status_code == 200
    body = response.json()
    assert "picks_logged" in body
    assert "edge_buckets" in body


def test_summarize_clv_hit_rate(isolated_clv_log):
    fc._append_row(
        {
            "pick_id": "2026-06-10:777001:home",
            "board_date": "2026-06-10",
            "side": "home",
            "american_odds_at_pick": 105,
            "pick_won": True,
        }
    )
    fc._append_row(
        {
            "pick_id": "2026-06-10:777002:away",
            "board_date": "2026-06-10",
            "side": "away",
            "american_odds_at_pick": 120,
            "pick_won": False,
        }
    )
    summary = fc.summarize_clv(days=30)
    assert summary["picks_settled"] == 2
    assert summary["hit_rate"] == 0.5


def test_build_daily_board_cache_hit_logs_live_picks(isolated_clv_log, monkeypatch, tmp_path):
    cache_file = tmp_path / "daily_board.json"
    monkeypatch.setattr("app.services.daily_board.DAILY_BOARD_CACHE", cache_file)
    cached = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "cache_key": "2026-06-10_live_no_totals_edge0.08_parlays5",
        "date": "2026-06-10",
        "mode": "live",
        "odds_source": "the_odds_api",
        "slate": [SAMPLE_SLATE_GAME],
    }
    cache_file.write_text(__import__("json").dumps(cached), encoding="utf-8")
    with patch("app.services.daily_board.live_odds_enabled", return_value=True):
        with patch("app.services.daily_board.log_live_picks") as mock_log:
            build_daily_board(
                game_date=date(2026, 6, 10),
                use_cache=False,
                refresh=False,
                skip_totals=True,
            )
            mock_log.assert_called_once()
