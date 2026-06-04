import pytest

from app.odds.odds_math import (
    american_to_decimal,
    joint_probability,
    parlay_decimal_payout,
    parlay_ev,
)


def test_joint_probability_independence():
    assert joint_probability([0.6, 0.5]) == pytest.approx(0.3)


def test_parlay_decimal_and_ev_example():
    # Two +100 legs: decimal 2.0 each, joint model prob 0.25
    legs = [100, 100]
    decimal = parlay_decimal_payout(legs)
    assert decimal == pytest.approx(4.0)
    model_joint = joint_probability([0.5, 0.5])
    assert model_joint == pytest.approx(0.25)
    assert parlay_ev(model_joint, decimal) == pytest.approx(0.0)

    model_joint_better = joint_probability([0.55, 0.55])
    assert parlay_ev(model_joint_better, decimal) == pytest.approx(0.21)


def test_american_to_decimal_favorite():
    assert american_to_decimal(-200) == pytest.approx(1.5)
