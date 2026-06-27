"""Model probability estimates for over/under sides."""

from __future__ import annotations

import math
from typing import Any


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def _poisson_cdf_leq(k: int, lam: float) -> float:
    return sum(_poisson_pmf(i, lam) for i in range(max(0, k) + 1))


def _is_half_line(line: float) -> bool:
    return abs(line * 2 - round(line * 2)) < 1e-9 and abs(line - round(line)) > 1e-9


def side_win_probability(
    line: float,
    side: str,
    *,
    projection: float,
    std_dev: float | None = None,
    empirical_values: list[float] | None = None,
) -> float:
    """
    Estimate P(side wins) using Poisson + optional empirical blend.

    Whole-number lines exclude pushes from win probability.
    """
    lam = max(projection, 0.05)

    if _is_half_line(line):
        threshold = int(line) + 1
        if side == "over":
            prob = 1.0 - _poisson_cdf_leq(threshold - 1, lam)
        else:
            prob = _poisson_cdf_leq(threshold - 1, lam)
    else:
        whole = int(round(line))
        if side == "over":
            prob = 1.0 - _poisson_cdf_leq(whole, lam)
        else:
            prob = _poisson_cdf_leq(whole - 1, lam)

    if empirical_values:
        emp = _empirical_side_rate(empirical_values, line, side)
        if emp is not None:
            prob = 0.55 * prob + 0.45 * emp

    return round(max(0.001, min(0.999, prob)), 4)


def _empirical_side_rate(values: list[float], line: float, side: str) -> float | None:
    if not values:
        return None
    wins = 0
    counted = 0
    for stat in values:
        if _is_half_line(line):
            if side == "over":
                wins += 1 if stat > line else 0
                counted += 1
            else:
                wins += 1 if stat < line else 0
                counted += 1
        else:
            whole = int(round(line))
            if side == "over":
                if stat > whole:
                    wins += 1
                elif stat != whole:
                    counted += 1
                counted += 1 if stat != whole else 0
            else:
                if stat < whole:
                    wins += 1
                counted += 1 if stat != whole else 0
    if counted == 0:
        return None
    return wins / counted


def model_probabilities(
    line: float,
    *,
    projection: float,
    std_dev: float | None = None,
    empirical_values: list[float] | None = None,
) -> dict[str, float]:
    over = side_win_probability(
        line, "over", projection=projection, std_dev=std_dev, empirical_values=empirical_values
    )
    under = side_win_probability(
        line, "under", projection=projection, std_dev=std_dev, empirical_values=empirical_values
    )
    total = over + under
    if total > 0 and abs(total - 1.0) > 0.05:
        over = over / total
        under = under / total
    return {
        "model_probability_over": round(over, 4),
        "model_probability_under": round(under, 4),
    }
