"""Prop tracker auto-refresh scheduler tests."""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.services import prop_tracker_refresh as ptr


@pytest.fixture
def isolated_tracker_status(tmp_path, monkeypatch):
    status_path = tmp_path / "last_prop_tracker_refresh.json"
    monkeypatch.setattr(ptr, "LAST_PROP_TRACKER_REFRESH", status_path)
    return status_path


def test_prop_tracker_auto_enabled_default(monkeypatch):
    monkeypatch.delenv("PROP_TRACKER_AUTO", raising=False)
    assert ptr.prop_tracker_auto_enabled() is True
    monkeypatch.setenv("PROP_TRACKER_AUTO", "false")
    assert ptr.prop_tracker_auto_enabled() is False


def test_run_skips_when_disabled(isolated_tracker_status, monkeypatch):
    monkeypatch.setenv("PROP_TRACKER_AUTO", "false")
    code = ptr.run_prop_tracker_refresh(date(2026, 6, 16))
    assert code == 0
    status = ptr.load_prop_tracker_refresh_status()
    assert status["skipped"] == "PROP_TRACKER_AUTO=false"


def test_run_skips_recent_refresh(isolated_tracker_status, monkeypatch):
    monkeypatch.setenv("PROP_TRACKER_MIN_SECONDS", "3600")
    isolated_tracker_status.write_text(
        '{"ran_at": "2099-01-01T12:00:00+00:00", "ok": true, "date": "2099-01-01"}',
        encoding="utf-8",
    )
    with patch.object(ptr, "backfill_prop_results") as mock_backfill:
        code = ptr.run_prop_tracker_refresh(date(2099, 1, 1))
    assert code == 0
    mock_backfill.assert_not_called()


def test_run_backfills_and_writes_status(isolated_tracker_status, monkeypatch):
    monkeypatch.setenv("PROP_TRACKER_MIN_SECONDS", "0")
    with (
        patch.object(
            ptr,
            "backfill_prop_results",
            return_value={"updated": 2, "pending": 1, "dnp": 0},
        ),
        patch.object(
            ptr,
            "summarize_prop_tracker",
            return_value={"props_settled": 5, "overall_hit_rate": 0.6},
        ),
    ):
        code = ptr.run_prop_tracker_refresh(date(2026, 6, 16))
    assert code == 0
    status = ptr.load_prop_tracker_refresh_status()
    assert status["ok"] is True
    assert status["backfill"]["updated"] == 2
    assert status["props_settled"] == 5
    assert status["overall_hit_rate"] == 0.6
