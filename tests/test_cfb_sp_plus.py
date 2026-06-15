"""CFBD SP+ cache tests."""

import json

import pytest

from app.ingest.cfb_sp_plus import (
    SPPlusStore,
    TeamSPPlus,
    _parse_sp_row,
    fetch_and_cache_preseason_sp,
    load_sp_plus_store,
    pregame_sp_week,
    sp_plus_diffs_for_game,
)


def test_parse_sp_row():
    row = {
        "team": "Georgia",
        "rating": 28.5,
        "offense": {"rating": 35.0},
        "defense": {"rating": 22.0},
    }
    parsed = _parse_sp_row(row)
    assert parsed is not None
    team, sp = parsed
    assert team == "Georgia"
    assert sp.overall == 28.5


def test_pregame_sp_week():
    assert pregame_sp_week(1) == 0
    assert pregame_sp_week(5) == 4


def test_flat_mode_week1_only(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sp_cache"
    monkeypatch.setattr("app.ingest.cfb_sp_plus.SP_PLUS_CACHE_DIR", cache_dir)
    cache_dir.mkdir()
    preseason = {
        "season": 2025,
        "teams": [
            {"team": "Georgia", "overall": 10.0, "offense": 12.0, "defense": 8.0},
            {"team": "Alabama", "overall": 8.0, "offense": 9.0, "defense": 7.0},
        ],
    }
    (cache_dir / "2025_preseason.json").write_text(json.dumps(preseason), encoding="utf-8")
    (cache_dir / "2025_meta.json").write_text(
        json.dumps({"weekly_mode": "flat", "last_confirmed_week": None}),
        encoding="utf-8",
    )
    store = load_sp_plus_store((2025,))
    diff, _, _ = sp_plus_diffs_for_game(
        season=2025,
        game_week=1,
        home_team="Georgia",
        away_team="Alabama",
        lookup=store,
    )
    assert diff == pytest.approx(2.0)
    assert sp_plus_diffs_for_game(
        season=2025,
        game_week=3,
        home_team="Georgia",
        away_team="Alabama",
        lookup=store,
    ) == (0.0, 0.0, 0.0)


def test_fetch_preseason_roundtrip(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sp_cache"
    monkeypatch.setattr("app.ingest.cfb_sp_plus.SP_PLUS_CACHE_DIR", cache_dir)

    def _fake_fetch(client, season, *, api_key, week=None):
        return {"Georgia": TeamSPPlus(overall=10.0, offense=12.0, defense=8.0)}

    monkeypatch.setattr("app.ingest.cfb_sp_plus._fetch_sp_plus", _fake_fetch)
    ratings = fetch_and_cache_preseason_sp(2025, api_key="test-key")
    assert "Georgia" in ratings
    store = load_sp_plus_store((2025,))
    assert (2025, "Georgia") in store.preseason
