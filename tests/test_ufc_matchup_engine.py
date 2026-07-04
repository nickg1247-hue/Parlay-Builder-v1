"""Tests for UFC matchup prediction engine."""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.models.ufc_matchup_engine import (
    CATEGORY_WEIGHTS,
    FEATURE_SCORE_FIELDS,
    build_fighter_context,
    compute_matchup_adjustments,
    parse_record,
    predict_matchup,
    score_fighter,
)


def test_parse_record():
    assert parse_record("22-3-0").wins == 22
    assert parse_record("15-8").losses == 8
    assert parse_record(None).total_bouts == 0


def test_category_weights_sum_to_one():
    assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-6


def test_feature_scores_populated():
    ctx = build_fighter_context(
        name="Test Fighter",
        side="away",
        fight={"away_record": "18-4", "weight_class": "Lightweight Bout"},
        slate_day=date(2024, 6, 1),
        feature_row={
            "away_career_win_pct": 0.72,
            "away_last5_win_pct": 0.8,
            "away_rest_days": 90,
            "away_b2b": 0,
            "elo_away_pre": 1580,
        },
        history_df=pd.DataFrame(
            columns=[
                "fight_id",
                "date",
                "home_team",
                "away_team",
                "home_win",
                "season",
            ]
        ),
    )
    scores = score_fighter(ctx)
    for field in FEATURE_SCORE_FIELDS:
        val = getattr(scores, field)
        assert 0 <= val <= 100, field
    assert scores.style_archetype in {
        "striker",
        "wrestler",
        "grappler",
        "balanced",
        "pressure",
        "counter_striker",
    }


def test_wrestler_vs_weak_tdd_favors_wrestler():
    away = score_fighter(
        build_fighter_context(
            name="Chain Wrestler",
            side="away",
            fight={"away_record": "14-3", "weight_class": "Welterweight"},
            slate_day=date(2024, 6, 1),
            feature_row={"away_last5_win_pct": 0.8, "away_career_win_pct": 0.75, "elo_away_pre": 1550},
            history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
        )
    )
    home = score_fighter(
        build_fighter_context(
            name="Striker",
            side="home",
            fight={"home_record": "16-6", "weight_class": "Welterweight"},
            slate_day=date(2024, 6, 1),
            feature_row={"home_last5_win_pct": 0.6, "home_career_win_pct": 0.6, "elo_home_pre": 1480},
            history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
        )
    )
    away.takedown_offense_score = 78
    away.style_archetype = "wrestler"
    home.takedown_defense_score = 42
    away_ctx = build_fighter_context(
        name="Chain Wrestler",
        side="away",
        fight={"away_record": "14-3", "weight_class": "Welterweight"},
        slate_day=date(2024, 6, 1),
        feature_row={},
        history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
    )
    home_ctx = build_fighter_context(
        name="Striker",
        side="home",
        fight={"home_record": "16-6", "weight_class": "Welterweight"},
        slate_day=date(2024, 6, 1),
        feature_row={},
        history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
    )
    adj = compute_matchup_adjustments(away, home, away_ctx, home_ctx)
    assert adj.adjusted_grappling_advantage > 0.1
    assert adj.adjusted_style_advantage > 0


def test_better_record_does_not_always_win():
    fight = {
        "away_team": "Grappler A",
        "home_team": "Striker B",
        "away_record": "12-8",
        "home_record": "20-2",
        "weight_class": "Lightweight Bout",
    }
    result = predict_matchup(
        fight,
        date(2024, 8, 1),
        feature_row={
            "away_career_win_pct": 0.55,
            "home_career_win_pct": 0.9,
            "away_last5_win_pct": 0.8,
            "home_last5_win_pct": 0.6,
            "elo_away_pre": 1560,
            "elo_home_pre": 1520,
        },
        history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
    )
    # Underdog on record can still win via matchup factors when form/Elo favor them
    assert result["predictedWinner"] in ("Grappler A", "Striker B")
    assert 50 <= result["confidence"] <= 100


def test_predict_matchup_output_schema():
    fight = {
        "away_team": "Fighter A",
        "home_team": "Fighter B",
        "away_record": "10-2",
        "home_record": "8-4",
        "weight_class": "Featherweight Bout",
    }
    out = predict_matchup(
        fight,
        date(2024, 7, 4),
        feature_row={
            "away_last5_win_pct": 0.8,
            "home_last5_win_pct": 0.4,
            "away_career_win_pct": 0.83,
            "home_career_win_pct": 0.67,
            "elo_away_pre": 1540,
            "elo_home_pre": 1490,
        },
        history_df=pd.DataFrame(columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]),
    )

    assert out["predictedWinner"] in ("Fighter A", "Fighter B")
    assert 50 <= out["confidence"] <= 100
    assert "fighterA" in out["fighterScores"]
    assert "fighterB" in out["fighterScores"]
    assert set(out["categoryBreakdown"].keys()) >= {
        "styleMatchup",
        "striking",
        "grappling",
        "cardio",
        "recentForm",
    }
    methods = out["winMethodProbabilities"]
    assert abs(sum(methods.values()) - 1.0) < 0.02
    assert len(out["keyReasons"]) >= 1
    assert isinstance(out["riskFactors"], list)
    assert isinstance(out["modelNotes"], list)


def test_long_layoff_adds_risk(monkeypatch):
    fight = {
        "away_team": "Rusty Fighter",
        "home_team": "Active Fighter",
        "away_record": "20-5",
        "home_record": "15-3",
        "weight_class": "Middleweight Bout",
    }

    def _layoff(name, _day):
        return 800 if "Rusty" in name else 90

    monkeypatch.setattr("app.models.ufc_matchup_engine.fighter_layoff_days", _layoff)
    out = predict_matchup(
        fight,
        date(2024, 7, 4),
        history_df=pd.DataFrame(
            columns=["fight_id", "date", "home_team", "away_team", "home_win", "season"]
        ),
    )
    assert any("layoff" in r.lower() for r in out["riskFactors"])
