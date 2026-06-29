"""ML actionable pick gate tests."""

import pytest

from app.models.ml_pick_gates import is_actionable_ml_pick


@pytest.mark.parametrize(
    "prob,edge,expected",
    [
        (0.58, 0.10, True),
        (0.55, 0.08, True),
        (0.54, 0.08, False),
        (0.52, 0.10, True),
        (0.52, 0.09, False),
        (0.60, 0.05, False),
        (None, 0.12, False),
        (0.58, None, False),
    ],
)
def test_is_actionable_ml_pick(prob, edge, expected):
    assert is_actionable_ml_pick(prob, edge) is expected
