"""OOS NFL prop backtest — cache only, no scoring changes."""

from datetime import date
from pathlib import Path

from app.services.nfl_props_backtest import run_nfl_props_backtest


def test_empty_cache_is_honest(tmp_path: Path):
    empty = tmp_path / "nfl"
    empty.mkdir()
    report = run_nfl_props_backtest(
        as_of=date(2026, 8, 28),
        cache_dir=empty,
        write_report=False,
        stat_fn=lambda *a, **k: 99.0,
    )
    assert report["empty_reason"] == "no_cache"
    assert report["n_decided"] == 0
    assert report["formula_changed"] is False


def test_grades_cached_recommended_side(tmp_path: Path):
    day = tmp_path / "2026-08-20"
    day.mkdir()
    (day / "g1.draftkings.json").write_text(
        """
        {"props": [
          {"game_id": "g1", "player": "A", "market_type": "player_rush_yds",
           "line": 50.5, "recommended_side": "over", "prop_score": 80,
           "recommended_odds": -110, "model_probability": 0.62,
           "market_probability": 0.52, "team": "CLE"},
          {"game_id": "g1", "player": "A", "market_type": "player_rush_yds",
           "line": 50.5, "recommended_side": "under", "prop_score": 20,
           "recommended_odds": -110, "model_probability": 0.38,
           "market_probability": 0.48, "team": "CLE"}
        ]}
        """,
        encoding="utf-8",
    )
    report = run_nfl_props_backtest(
        as_of=date(2026, 8, 28),
        cache_dir=tmp_path,
        write_report=False,
        stat_fn=lambda player, market, day, team_abbr=None: 80.0,
    )
    assert report["n_decided"] == 1
    assert report["hit_rate"] == 1.0
    assert report["rows"][0]["side"] == "over"
    assert report["plus_ev_n"] == 1
    assert report["formula_changed"] is False
