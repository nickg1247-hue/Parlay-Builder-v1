import pytest

from app.odds.spread_math import (
    market_probs_from_american_spread,
    model_prob_home_cover,
    side_covers,
)


def test_side_covers_home_minus_15_covers_win_by_two():
    assert side_covers("home", 5, 3, -1.5, 1.5) is True


def test_side_covers_away_plus_15_covers_loss_by_one():
    assert side_covers("away", 5, 4, -1.5, 1.5) is True
    assert side_covers("home", 5, 4, -1.5, 1.5) is False


def test_market_probs_from_american_spread_remove_vig():
    home, away = market_probs_from_american_spread(-1.5, -110, 1.5, -110)
    assert home == pytest.approx(0.5, rel=1e-5)
    assert away == pytest.approx(0.5, rel=1e-5)
    assert home + away == pytest.approx(1.0)


def test_model_prob_home_cover_at_minus_15():
    # margin mean 2, std 1 → P(margin > 1.5) for home -1.5
    p = model_prob_home_cover(2.0, 1.0, -1.5)
    assert 0.4 < p < 0.7
