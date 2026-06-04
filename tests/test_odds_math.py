import pytest

from app.odds.team_aliases import is_valid_american_odds
from app.odds.odds_math import (
    american_payout_profit,
    american_to_implied_prob,
    market_probs_from_american,
    remove_vig,
)


def test_american_to_implied_favorite_and_dog():
    assert american_to_implied_prob(-150) == pytest.approx(0.6, rel=1e-3)
    assert american_to_implied_prob(150) == pytest.approx(0.4, rel=1e-3)


def test_remove_vig_normalizes():
    home, away = remove_vig(0.6, 0.45)
    assert home == pytest.approx(4 / 7, rel=1e-5)
    assert away == pytest.approx(3 / 7, rel=1e-5)
    assert home + away == pytest.approx(1.0)


def test_market_probs_from_american_even_line():
    home, away = market_probs_from_american(-110, -110)
    assert home == pytest.approx(0.5, rel=1e-5)
    assert away == pytest.approx(0.5, rel=1e-5)


def test_valid_american_odds_filter():
    assert is_valid_american_odds(-110)
    assert is_valid_american_odds(150)
    assert not is_valid_american_odds(-1)
    assert not is_valid_american_odds(0)
    assert not is_valid_american_odds(600)


def test_american_payout_profit():
    assert american_payout_profit(-110, True) == pytest.approx(100 / 110, rel=1e-5)
    assert american_payout_profit(150, True) == pytest.approx(1.5, rel=1e-5)
    assert american_payout_profit(-110, False) == -1.0
