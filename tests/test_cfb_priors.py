"""CFB prior cache and feature helpers."""

import pytest

from app.features.cfb_pregame import conference_tier, prior_blend_weight
from app.ingest.cfb_priors import PriorsStore, prior_feature_diffs, prior_seasons_for


def test_prior_seasons_include_lookback():
    assert prior_seasons_for((2022, 2025)) == tuple(range(2021, 2026))


def test_prior_blend_weight_schedule():
    assert prior_blend_weight(1) == 0.70
    assert prior_blend_weight(3) == 0.70
    assert prior_blend_weight(8) == 0.30
    assert prior_blend_weight(15) == 0.30
    mid = prior_blend_weight(5)
    assert 0.30 < mid < 0.70


def test_conference_tiers():
    assert conference_tier("SEC") == 3
    assert conference_tier("Big Ten") == 3
    assert conference_tier("Sun Belt") == 2
    assert conference_tier("FBS Independents") == 2
    assert conference_tier("Southern") == 1
    assert conference_tier("") == 1


def test_prior_feature_diffs_use_prior_year_fpi_only():
    store = PriorsStore()
    store.talent[(2025, "Georgia")] = 980.0
    store.talent[(2025, "Alabama")] = 960.0
    store.returning_pct[(2025, "Georgia")] = 0.70
    store.returning_pct[(2025, "Alabama")] = 0.40
    store.returning_pass_pct[(2025, "Georgia")] = 0.80
    store.returning_pass_pct[(2025, "Alabama")] = 0.20
    store.fpi[(2025, "Georgia")] = 25.0
    store.fpi[(2024, "Georgia")] = 18.0
    store.fpi[(2024, "Alabama")] = 16.0
    store.coaches[(2025, "Georgia")] = "kirby smart"
    store.coaches[(2024, "Georgia")] = "kirby smart"
    store.coaches[(2025, "Alabama")] = "new coach"
    store.coaches[(2024, "Alabama")] = "old coach"

    diffs = prior_feature_diffs(
        season=2025,
        home_team="Georgia",
        away_team="Alabama",
        store=store,
    )
    assert diffs["talent_diff"] == 20.0
    assert diffs["returning_pct_diff"] == pytest.approx(0.30)
    assert diffs["prior_fpi_diff"] == 2.0
    assert diffs["coach_change_home"] == 0.0
    assert diffs["coach_change_away"] == 1.0
