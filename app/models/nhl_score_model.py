"""NHL score model: shrunk goal rates → Poisson grid → winner, puck line, total.

Separate from the NFL/NBA classifiers. Regulation goals are independent Poisson
draws. A tied regulation game becomes a one-goal overtime or shootout result.
"""

from __future__ import annotations

import math
from typing import Any

MAX_GOALS = 12
HOME_XG_MULT = 1.06
AWAY_XG_MULT = 0.97
OT_HOME_SHARE = 0.54
DEFAULT_TOTAL = 6.5
DEFAULT_PUCKLINE = 1.5
SHRINK_GAMES = 10
MODEL_NAME = "NHL Poisson score model"
MODEL_FAMILY = "nhl_poisson"


def poisson_probs(lam: float, n: int = MAX_GOALS) -> list[float]:
    """P(0)..P(n), with the missing tail folded into the last bin."""
    lam = max(0.05, float(lam))
    raw = [math.exp(-lam) * (lam**k) / math.factorial(k) for k in range(n + 1)]
    raw[-1] += max(0.0, 1.0 - sum(raw))
    total = sum(raw)
    return [p / total for p in raw]


def _confidence(prob: float) -> str:
    gap = abs(float(prob) - 0.5)
    if gap < 0.04:
        return "Low"
    if gap < 0.08:
        return "Medium"
    if gap < 0.14:
        return "High"
    return "Very high"


def _shrink(rate: float, games: int, league: float) -> float:
    g = max(int(games), 0)
    return (float(rate) * g + float(league) * SHRINK_GAMES) / (g + SHRINK_GAMES)


def expected_goals(
    home: dict[str, Any],
    away: dict[str, Any],
    league_gf: float,
    *,
    neutral_site: bool = False,
) -> tuple[float, float, dict[str, float]]:
    """Attack × opponent defense, then a home-ice bump."""
    league = max(1.5, float(league_gf))
    home_gf = _shrink(float(home.get("gf_per_game") or league), int(home.get("games") or 0), league)
    home_ga = _shrink(float(home.get("ga_per_game") or league), int(home.get("games") or 0), league)
    away_gf = _shrink(float(away.get("gf_per_game") or league), int(away.get("games") or 0), league)
    away_ga = _shrink(float(away.get("ga_per_game") or league), int(away.get("games") or 0), league)
    home_xg = league * (home_gf / league) * (away_ga / league)
    away_xg = league * (away_gf / league) * (home_ga / league)
    if not neutral_site:
        home_xg *= HOME_XG_MULT
        away_xg *= AWAY_XG_MULT
    home_xg = min(6.5, max(1.4, home_xg))
    away_xg = min(6.5, max(1.4, away_xg))
    return home_xg, away_xg, {
        "home_gf_shrunk": home_gf,
        "home_ga_shrunk": home_ga,
        "away_gf_shrunk": away_gf,
        "away_ga_shrunk": away_ga,
    }


def score_grid(home_xg: float, away_xg: float) -> dict[str, Any]:
    home_p = poisson_probs(home_xg)
    away_p = poisson_probs(away_xg)
    p_home_reg = 0.0
    p_away_reg = 0.0
    p_tie = 0.0
    p_home_by_2 = 0.0
    p_away_by_2 = 0.0
    exp_final_total = 0.0
    lines: list[tuple[float, int, int, str]] = []
    for h, ph in enumerate(home_p):
        for a, pa in enumerate(away_p):
            p = ph * pa
            if h > a:
                p_home_reg += p
                if h >= a + 2:
                    p_home_by_2 += p
                exp_final_total += p * (h + a)
                lines.append((p, h, a, "REG"))
            elif a > h:
                p_away_reg += p
                if a >= h + 2:
                    p_away_by_2 += p
                exp_final_total += p * (h + a)
                lines.append((p, h, a, "REG"))
            else:
                p_tie += p
                exp_final_total += p * (h + a + 1)
                lines.append((p * OT_HOME_SHARE, h + 1, a, "OT"))
                lines.append((p * (1.0 - OT_HOME_SHARE), h, a + 1, "OT"))
    p_home = p_home_reg + p_tie * OT_HOME_SHARE
    lines.sort(key=lambda row: row[0], reverse=True)
    top = [
        {
            "home_goals": h,
            "away_goals": a,
            "prob": round(p, 4),
            "period": tag,
        }
        for p, h, a, tag in lines[:5]
    ]
    return {
        "prob_home_win": p_home,
        "prob_away_win": 1.0 - p_home,
        "prob_regulation_tie": p_tie,
        "prob_home_win_by_2": p_home_by_2,
        "prob_away_win_by_2": p_away_by_2,
        "expected_final_total": exp_final_total,
        "scorelines": top,
    }


def _over_prob(home_xg: float, away_xg: float, line: float) -> float:
    home_p = poisson_probs(home_xg)
    away_p = poisson_probs(away_xg)
    p_over = 0.0
    for h, ph in enumerate(home_p):
        for a, pa in enumerate(away_p):
            total = h + a + (1 if h == a else 0)
            if total > line:
                p_over += ph * pa
    return p_over


def _fmt_points(points: float) -> str:
    text = f"{points:g}"
    return f"+{text}" if points > 0 else text


def predict_matchup(
    *,
    home_team: str,
    away_team: str,
    home_abbr: str,
    away_abbr: str,
    home_rating: dict[str, Any] | None,
    away_rating: dict[str, Any] | None,
    league_gf: float,
    neutral_site: bool = False,
    home_spread: float | None = None,
    ou_line: float | None = None,
    ratings_note: str = "",
) -> dict[str, Any]:
    league = float(league_gf or 3.05)
    home = home_rating or {"gf_per_game": league, "ga_per_game": league, "games": 0, "name": home_team}
    away = away_rating or {"gf_per_game": league, "ga_per_game": league, "games": 0, "name": away_team}
    home_xg, away_xg, shrunk = expected_goals(home, away, league, neutral_site=neutral_site)
    grid = score_grid(home_xg, away_xg)
    p_home = float(grid["prob_home_win"])
    pick_side = "home" if p_home >= 0.5 else "away"
    model_pick = home_team if pick_side == "home" else away_team

    spread_source = "book" if home_spread is not None else "model"
    if home_spread is None:
        home_spread = -DEFAULT_PUCKLINE if p_home >= 0.5 else DEFAULT_PUCKLINE
    if home_spread < 0:
        home_cover = float(grid["prob_home_win_by_2"])
        away_cover = 1.0 - home_cover
        away_points = abs(home_spread)
    else:
        away_cover = float(grid["prob_away_win_by_2"])
        home_cover = 1.0 - away_cover
        away_points = -abs(home_spread)
    if home_cover >= away_cover:
        spread_pick = f"{home_team} {_fmt_points(home_spread)}"
        spread_prob = home_cover
    else:
        spread_pick = f"{away_team} {_fmt_points(away_points)}"
        spread_prob = away_cover

    total_source = "book" if ou_line is not None else "model"
    line = float(ou_line if ou_line is not None else DEFAULT_TOTAL)
    p_over = _over_prob(home_xg, away_xg, line)
    if p_over >= 0.5:
        totals_pick = f"Over {line:g}"
        totals_prob = p_over
    else:
        totals_pick = f"Under {line:g}"
        totals_prob = 1.0 - p_over

    drivers = [
        {
            "label": f"{home_team} offense",
            "value": f"{float(home.get('gf_per_game') or league):.2f} GF/G · {int(home.get('games') or 0)} GP",
        },
        {
            "label": f"{home_team} defense",
            "value": f"{float(home.get('ga_per_game') or league):.2f} GA/G",
        },
        {
            "label": f"{away_team} offense",
            "value": f"{float(away.get('gf_per_game') or league):.2f} GF/G · {int(away.get('games') or 0)} GP",
        },
        {
            "label": f"{away_team} defense",
            "value": f"{float(away.get('ga_per_game') or league):.2f} GA/G",
        },
        {"label": "League average", "value": f"{league:.2f} goals per team"},
        {
            "label": "Home ice",
            "value": "Neutral site" if neutral_site else "Home expected goals +6%, away −3%",
        },
        {
            "label": "Expected score",
            "value": f"{away_abbr or 'Away'} {away_xg:.1f} – {home_abbr or 'Home'} {home_xg:.1f}",
        },
    ]
    return {
        "model_name": MODEL_NAME,
        "model_family": MODEL_FAMILY,
        "ratings_note": ratings_note,
        "model_prob_home": round(p_home, 4),
        "model_prob_away": round(1.0 - p_home, 4),
        "model_pick": model_pick,
        "model_pick_team": model_pick,
        "model_pick_side": pick_side,
        "model_confidence": _confidence(p_home),
        "ml_confidence": _confidence(p_home),
        "expected_home_goals": round(home_xg, 2),
        "expected_away_goals": round(away_xg, 2),
        "expected_total_goals": round(float(grid["expected_final_total"]), 2),
        "prob_regulation_tie": round(float(grid["prob_regulation_tie"]), 4),
        "home_spread_point": home_spread,
        "spread_line_source": spread_source,
        "model_prob_home_cover": round(home_cover, 4),
        "model_prob_away_cover": round(away_cover, 4),
        "spread_pick": spread_pick,
        "spread_confidence": _confidence(spread_prob),
        "ou_line": line,
        "ou_line_source": total_source,
        "model_prob_over": round(p_over, 4),
        "model_prob_under": round(1.0 - p_over, 4),
        "totals_pick": totals_pick,
        "totals_confidence": _confidence(totals_prob),
        "scorelines": grid["scorelines"],
        "drivers": drivers,
        "shrunk_rates": {k: round(v, 3) for k, v in shrunk.items()},
    }
