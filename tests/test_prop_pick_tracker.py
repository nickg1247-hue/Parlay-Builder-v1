"""Prop pick tracker logging and grading tests."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import prop_pick_tracker as ppt

client = TestClient(app)

SAMPLE_PROP = {
    "game_id": "777001",
    "matchup": "Boston Red Sox @ New York Yankees",
    "player": "Aaron Judge",
    "market_type": "batter_hits",
    "market_label": "Hits",
    "line": 1.5,
    "recommended_side": "over",
    "recommended_odds": -115,
    "recommended_hit_rate": 0.7,
    "score": 70,
    "actionable": True,
    "line_strength": "strong",
    "line_strength_label": "Strong line",
    "line_insight": "L10 hit rate 70%",
    "bookmaker": "draftkings",
}


@pytest.fixture
def isolated_prop_log(tmp_path, monkeypatch):
    log_path = tmp_path / "prop_pick_log.jsonl"
    monkeypatch.setattr(ppt, "PROP_PICK_LOG", log_path)
    return log_path


def test_grade_prop_result_over_hit():
    hit, status = ppt.grade_prop_result(2.0, 1.5, "over")
    assert hit is True
    assert status == "settled"


def test_grade_prop_result_under_miss():
    hit, status = ppt.grade_prop_result(2.0, 1.5, "under")
    assert hit is False
    assert status == "settled"


def test_grade_prop_result_push():
    hit, status = ppt.grade_prop_result(1.5, 1.5, "over")
    assert hit is None
    assert status == "push"


def test_log_offered_props_writes_strong_lines(isolated_prop_log):
    written = ppt.log_offered_props([SAMPLE_PROP], "2026-06-16", source="test")
    assert len(written) == 1
    assert written[0]["pick_id"].endswith(":draftkings")
    assert written[0]["result_status"] == "pending"


def test_log_skips_weak_unless_env(isolated_prop_log, monkeypatch):
    weak = {**SAMPLE_PROP, "line_strength": "weak"}
    assert ppt.log_offered_props([weak], "2026-06-16") == []
    monkeypatch.setenv("PROP_TRACK_ALL_ACTIONABLE", "true")
    assert len(ppt.log_offered_props([weak], "2026-06-16")) == 1


def test_log_idempotent_within_five_american_points(isolated_prop_log):
    ppt.log_offered_props([SAMPLE_PROP], "2026-06-16")
    moved = {**SAMPLE_PROP, "recommended_odds": -118}
    assert ppt.log_offered_props([moved], "2026-06-16") == []
    big_move = {**SAMPLE_PROP, "recommended_odds": -125}
    assert len(ppt.log_offered_props([big_move], "2026-06-16")) == 1


def test_backfill_sets_hit_fields(isolated_prop_log, monkeypatch):
    ppt.log_offered_props([SAMPLE_PROP], "2026-06-16")
    monkeypatch.setattr(
        ppt,
        "player_stat_on_date",
        lambda player, market, season, day: 2.0 if player == "Aaron Judge" else None,
    )
    result = ppt.backfill_prop_results(date(2026, 6, 16))
    assert result["updated"] == 1
    latest = ppt._latest_by_pick_id(ppt._read_all_rows())
    row = next(iter(latest.values()))
    assert row["hit"] is True
    assert row["actual_stat"] == 2.0
    assert row["result_status"] == "settled"


def test_summarize_by_line_strength(isolated_prop_log, monkeypatch):
    moderate = {**SAMPLE_PROP, "line_strength": "moderate", "player": "Mookie Betts"}
    ppt.log_offered_props([SAMPLE_PROP, moderate], "2026-06-15")
    monkeypatch.setattr(
        ppt,
        "player_stat_on_date",
        lambda player, market, season, day: 2.0 if player == "Aaron Judge" else 0.0,
    )
    ppt.backfill_prop_results(date(2026, 6, 15))
    summary = ppt.summarize_prop_tracker(days=30)
    assert summary["props_logged"] == 2
    assert summary["line_strength"]["strong"]["hits"] == 1
    assert summary["line_strength"]["moderate"]["misses"] == 1


def test_api_props_tracker_summary():
    response = client.get("/api/props/tracker/summary?days=30")
    assert response.status_code == 200
    body = response.json()
    assert "line_strength" in body
    assert "strong" in body["line_strength"]
