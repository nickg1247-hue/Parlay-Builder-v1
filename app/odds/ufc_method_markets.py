"""Parse UFC method-of-victory and round props from Odds API bookmaker payloads."""

from __future__ import annotations

import re
from typing import Any

from app.odds.odds_math import american_to_implied_prob
from app.odds.team_aliases import is_valid_american_odds
from app.odds.ufc_fighter_aliases import fighters_match, normalize_fighter_name

# Keys aligned with app.models.ufc_matchup_engine winMethodProbabilities
METHOD_KEYS: tuple[str, ...] = (
    "fighterA_KO_TKO",
    "fighterA_Submission",
    "fighterA_Decision",
    "fighterB_KO_TKO",
    "fighterB_Submission",
    "fighterB_Decision",
)

_METHOD_MARKET_KEYS = frozenset(
    {
        "method_of_victory",
        "winning_method",
        "fight_result",
        "player_method_of_victory",
        "method",
    }
)
_ROUND_MARKET_KEYS = frozenset(
    {
        "round_betting",
        "fight_goes_distance",
        "goes_the_distance",
        "distance",
    }
)

_KO_RE = re.compile(r"\b(ko|tko|knockout)\b", re.I)
_SUB_RE = re.compile(r"\bsubmission\b", re.I)
_DEC_RE = re.compile(r"\bdecision\b", re.I)
_DISTANCE_RE = re.compile(r"\b(goes the distance|to go the distance|distance)\b", re.I)


def _method_key_for_fighter(
    fighter_side: str,
    method_kind: str,
) -> str | None:
    prefix = "fighterA" if fighter_side == "away" else "fighterB"
    if method_kind == "ko":
        return f"{prefix}_KO_TKO"
    if method_kind == "sub":
        return f"{prefix}_Submission"
    if method_kind == "dec":
        return f"{prefix}_Decision"
    return None


def _classify_method_outcome(
    outcome_name: str,
    home: str,
    away: str,
) -> str | None:
    """Return METHOD_KEYS entry or None."""
    name = str(outcome_name or "").strip()
    if not name:
        return None

    lower = name.lower()
    if _DISTANCE_RE.search(lower) and not (_KO_RE.search(lower) or _SUB_RE.search(lower)):
        return None

    side: str | None = None
    if fighters_match(away, name) or away.lower() in lower:
        side = "away"
    elif fighters_match(home, name) or home.lower() in lower:
        side = "home"
    else:
        # "KO/TKO" only outcomes without fighter name — skip (ambiguous)
        return None

    if _KO_RE.search(lower):
        return _method_key_for_fighter(side, "ko")
    if _SUB_RE.search(lower):
        return _method_key_for_fighter(side, "sub")
    if _DEC_RE.search(lower):
        return _method_key_for_fighter(side, "dec")
    if " by " in lower:
        # "Fighter Name by Points" etc.
        if "point" in lower or "decision" in lower:
            return _method_key_for_fighter(side, "dec")
    return None


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    return sorted(values)[len(values) // 2]


def extract_method_props_from_bookmakers(
    bookmakers: list[dict[str, Any]],
    *,
    home: str,
    away: str,
) -> dict[str, int]:
    """Median American odds per method key across bookmakers."""
    home_n = normalize_fighter_name(home)
    away_n = normalize_fighter_name(away)
    by_key: dict[str, list[int]] = {k: [] for k in METHOD_KEYS}

    for book in bookmakers:
        for market in book.get("markets", []):
            key = str(market.get("key") or "").lower()
            if key not in _METHOD_MARKET_KEYS and key not in _ROUND_MARKET_KEYS:
                continue
            for outcome in market.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue
                try:
                    am = int(price)
                except (TypeError, ValueError):
                    continue
                if not is_valid_american_odds(am):
                    continue
                name = outcome.get("name") or outcome.get("description") or ""
                method_key = _classify_method_outcome(name, home_n, away_n)
                if method_key:
                    by_key[method_key].append(am)

    out: dict[str, int] = {}
    for mk, prices in by_key.items():
        med = _median_int(prices)
        if med is not None:
            out[mk] = med
    return out


def extract_goes_distance_odds(
    bookmakers: list[dict[str, Any]],
) -> dict[str, int | None]:
    """Yes/No American odds for fight goes the distance when offered."""
    yes_prices: list[int] = []
    no_prices: list[int] = []
    for book in bookmakers:
        for market in book.get("markets", []):
            key = str(market.get("key") or "").lower()
            if key not in _ROUND_MARKET_KEYS:
                continue
            for outcome in market.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue
                try:
                    am = int(price)
                except (TypeError, ValueError):
                    continue
                if not is_valid_american_odds(am):
                    continue
                name = str(outcome.get("name") or "").lower()
                if name in ("yes", "over"):
                    yes_prices.append(am)
                elif name in ("no", "under"):
                    no_prices.append(am)
    return {
        "goes_distance_yes": _median_int(yes_prices),
        "goes_distance_no": _median_int(no_prices),
    }


def method_prop_edges(
    model_probs: dict[str, float],
    market_odds: dict[str, int],
    *,
    min_edge: float = 0.08,
) -> list[dict[str, Any]]:
    """Compare model method probabilities to single-sided market implied probs."""
    labels = {
        "fighterA_KO_TKO": "Fighter A by KO/TKO",
        "fighterA_Submission": "Fighter A by Submission",
        "fighterA_Decision": "Fighter A by Decision",
        "fighterB_KO_TKO": "Fighter B by KO/TKO",
        "fighterB_Submission": "Fighter B by Submission",
        "fighterB_Decision": "Fighter B by Decision",
    }
    props: list[dict[str, Any]] = []
    for key in METHOD_KEYS:
        odds = market_odds.get(key)
        model_p = model_probs.get(key)
        if odds is None or model_p is None:
            continue
        market_p = american_to_implied_prob(int(odds))
        edge = float(model_p) - market_p
        props.append(
            {
                "market": "method",
                "method_key": key,
                "label": labels.get(key, key),
                "american_odds": int(odds),
                "model_prob": round(float(model_p), 4),
                "market_prob": round(market_p, 4),
                "edge": round(edge, 4),
                "plus_ev": edge >= min_edge,
            }
        )
    props.sort(key=lambda p: p["edge"], reverse=True)
    return props


def round_totals_edge(
    *,
    totals_line: float,
    over_odds: int,
    under_odds: int,
    model_over_prob: float,
    min_edge: float = 0.08,
) -> dict[str, Any] | None:
    """Edge on round O/U when model over probability is available."""
    from app.odds.odds_math import market_probs_from_american_totals

    mkt_over, mkt_under = market_probs_from_american_totals(over_odds, under_odds)
    edge_over = model_over_prob - mkt_over
    edge_under = (1.0 - model_over_prob) - mkt_under
    if edge_over >= edge_under:
        side = "over"
        edge = edge_over
        odds = over_odds
        model_p = model_over_prob
        market_p = mkt_over
    else:
        side = "under"
        edge = edge_under
        odds = under_odds
        model_p = 1.0 - model_over_prob
        market_p = mkt_under
    return {
        "market": "rounds_total",
        "side": side,
        "line": float(totals_line),
        "label": f"{'Over' if side == 'over' else 'Under'} {totals_line:g} rounds",
        "american_odds": int(odds),
        "over_odds": int(over_odds),
        "under_odds": int(under_odds),
        "model_prob": round(model_p, 4),
        "market_prob": round(market_p, 4),
        "edge": round(edge, 4),
        "plus_ev": edge >= min_edge,
    }


def model_over_rounds_prob(
    win_method_probs: dict[str, float],
    *,
    totals_line: float,
) -> float:
    """Heuristic P(total rounds > line) from method probabilities."""
    decision = float(win_method_probs.get("fighterA_Decision", 0)) + float(
        win_method_probs.get("fighterB_Decision", 0)
    )
    # Finishes skew under; decisions skew over — scale by line distance from 2.5
    finish = 1.0 - decision
    line = float(totals_line)
    if line <= 1.5:
        over = finish * 0.35 + decision * 0.15
    elif line <= 2.5:
        over = finish * 0.22 + decision * 0.72
    else:
        over = finish * 0.12 + decision * 0.88
    return max(0.05, min(0.95, over))
