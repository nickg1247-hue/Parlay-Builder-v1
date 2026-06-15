"""Tests for SP+ leakage audit helpers."""

import json
from pathlib import Path

import pytest

from app.ingest.cfb_sp_plus import (
    SPPlusStore,
    audit_season_week_files,
    compare_week_rating_files,
    run_sp_leakage_audit,
    sp_plus_diffs_for_game,
    week_files_for_season,
)


def _write_week(cache_dir: Path, season: int, week: int, ratings: dict[str, float]) -> Path:
    path = cache_dir / f"{season}_week_{week}.json"
    payload = {
        "season": season,
        "week": week,
        "teams": [
            {"team": team, "overall": val, "offense": val, "defense": val}
            for team, val in ratings.items()
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_preseason(cache_dir: Path, season: int, ratings: dict[str, float]) -> None:
    path = cache_dir / f"{season}_preseason.json"
    payload = {
        "season": season,
        "teams": [
            {"team": team, "overall": val, "offense": val, "defense": val}
            for team, val in ratings.items()
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_audit_differing_weeks_ok(tmp_path):
    _write_week(tmp_path, 2024, 1, {"Georgia": 20.0, "Alabama": 18.0})
    _write_week(tmp_path, 2024, 2, {"Georgia": 21.0, "Alabama": 18.5})
    files = week_files_for_season(2024, tmp_path)
    report = audit_season_week_files(2024, files)
    assert report["leakage_confirmed"] is False
    assert report["weekly_mode"] == "ok"
    assert report["last_confirmed_week"] == 2

    exit_code, reports = run_sp_leakage_audit(tmp_path)
    assert exit_code == 0
    assert reports[0]["weekly_mode"] == "ok"


def test_audit_identical_weeks_leaky(tmp_path):
    ratings = {"Georgia": 24.3, "Alabama": 25.0}
    _write_week(tmp_path, 2024, 1, ratings)
    _write_week(tmp_path, 2024, 2, ratings)
    _write_week(tmp_path, 2024, 3, ratings)
    files = week_files_for_season(2024, tmp_path)
    report = audit_season_week_files(2024, files)
    assert report["leakage_confirmed"] is True
    assert report["weekly_mode"] == "flat"
    assert report["last_confirmed_week"] is None

    left, right = files[0], files[1]
    cmp = compare_week_rating_files(left, right)
    assert cmp["max_abs_diff"] == pytest.approx(0.0)

    exit_code, reports = run_sp_leakage_audit(tmp_path)
    assert exit_code == 1
    assert reports[0]["leakage_confirmed"] is True


def test_sp_plus_flat_mode_week1_preseason_only(tmp_path, monkeypatch):
    monkeypatch.setattr("app.ingest.cfb_sp_plus.SP_PLUS_CACHE_DIR", tmp_path)
    ratings = {"Georgia": 24.0, "Alabama": 20.0}
    _write_week(tmp_path, 2025, 1, ratings)
    _write_week(tmp_path, 2025, 2, ratings)
    _write_preseason(tmp_path, 2025, {"Georgia": 10.0, "Alabama": 8.0})
    (tmp_path / "2025_meta.json").write_text(
        json.dumps({"weekly_mode": "flat", "last_confirmed_week": None}),
        encoding="utf-8",
    )

    from app.ingest.cfb_sp_plus import TeamSPPlus

    store = SPPlusStore(
        weekly_mode={2025: "flat"},
        preseason={
            (2025, "Georgia"): TeamSPPlus(10.0, 10.0, 10.0),
            (2025, "Alabama"): TeamSPPlus(8.0, 8.0, 8.0),
        },
        last_confirmed_week={2025: None},
    )

    w1 = sp_plus_diffs_for_game(
        season=2025,
        game_week=1,
        home_team="Georgia",
        away_team="Alabama",
        lookup=store,
    )
    assert w1[0] == pytest.approx(2.0)

    w5 = sp_plus_diffs_for_game(
        season=2025,
        game_week=5,
        home_team="Georgia",
        away_team="Alabama",
        lookup=store,
    )
    assert w5 == (0.0, 0.0, 0.0)


def test_sp_plus_ok_mode_uses_prior_week(tmp_path):
    _write_week(tmp_path, 2024, 3, {"Georgia": 22.0, "Alabama": 20.0})
    _write_week(tmp_path, 2024, 4, {"Georgia": 23.0, "Alabama": 19.0})
    store = SPPlusStore(weekly_mode={2024: "ok"})
    from app.ingest.cfb_sp_plus import TeamSPPlus, _load_cache_file

    for path in week_files_for_season(2024, tmp_path):
        _, week = path.stem.split("_week_")[0], int(path.stem.split("_week_")[1])
        for team, sp in _load_cache_file(path).items():
            store.weekly[(2024, week, team)] = sp

    diff, _, _ = sp_plus_diffs_for_game(
        season=2024,
        game_week=5,
        home_team="Georgia",
        away_team="Alabama",
        lookup=store,
    )
    assert diff == pytest.approx(4.0)
