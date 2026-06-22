"""Auto-build multi-leg prop parlays from the full daily slate."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.odds.odds_math import american_to_decimal
from app.services.props_mlb import (
    evaluate_prop_parlay,
    prop_is_bettable,
    prop_rank_key,
    prop_slip_leg,
    search_daily_props,
)


def _norm_player(name: str | None) -> str:
    return str(name or "").strip().lower()


def _form_score(prop: dict[str, Any]) -> float:
    l5, l10, season = prop.get("hit_rate_over_l10"), prop.get("hit_rate_over_l10"), prop.get("hit_rate_over_season")
    side = prop.get("recommended_side") or "over"
    if side == "under":
        l5 = prop.get("hit_rate_under_l5")
        l10 = prop.get("hit_rate_under_l10") or prop.get("recommended_hit_rate")
        season = prop.get("hit_rate_under_season")
    else:
        l5 = prop.get("hit_rate_over_l5")
        l10 = prop.get("hit_rate_over_l10") or prop.get("recommended_hit_rate")
        season = prop.get("hit_rate_over_season")
    l5f, l10f, sf = float(l5 or 0), float(l10 or 0), float(season or 0)
    return l10f * 0.55 + l5f * 0.30 + sf * 0.15


def _best_prop_per_player(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for prop in pool:
        player = _norm_player(prop.get("player"))
        if not player:
            continue
        prev = best.get(player)
        if prev is None or prop_rank_key(prop) < prop_rank_key(prev):
            best[player] = prop
    return list(best.values())


def _leg_from_prop(prop: dict[str, Any]) -> dict[str, Any]:
    gid = str(prop.get("game_id") or "")
    book = str(prop.get("bookmaker") or "draftkings")
    return prop_slip_leg(
        prop,
        game_id=gid,
        matchup=prop.get("matchup"),
        bookmaker=book,
        game_date=prop.get("game_date") or prop.get("date"),
    )


def _select_for_target_odds(
    candidates: list[dict[str, Any]],
    leg_count: int,
    target_american: int,
) -> list[dict[str, Any]]:
    """Pick unique-player legs balancing form vs per-leg odds fit."""
    target_dec = american_to_decimal(int(target_american))
    if target_dec <= 1.0 or leg_count < 1:
        candidates.sort(key=lambda p: (-_form_score(p), prop_rank_key(p)))
        return candidates[:leg_count]

    per_leg_dec = target_dec ** (1.0 / leg_count)

    def fit_key(prop: dict[str, Any]) -> tuple[float, float, float]:
        odds = prop.get("recommended_odds")
        if odds is None:
            return (-999.0, 0.0, 0.0)
        dec = american_to_decimal(int(odds))
        odds_penalty = abs(math.log(max(dec, 1.01) / max(per_leg_dec, 1.01)))
        form = _form_score(prop)
        return (-form + odds_penalty * 0.35, -form, prop_rank_key(prop)[0])

    ranked = sorted(candidates, key=fit_key)
    chosen: list[dict[str, Any]] = []
    used: set[str] = set()
    for prop in ranked:
        player = _norm_player(prop.get("player"))
        if not player or player in used:
            continue
        chosen.append(prop)
        used.add(player)
        if len(chosen) >= leg_count:
            break
    return chosen


def build_auto_prop_parlay(
    leg_count: int,
    *,
    target_american: int | None = None,
    bookmaker: str | None = None,
    game_date: date | None = None,
) -> dict[str, Any]:
    """
    Scan the full slate and assemble the best N-leg prop parlay.

    One leg per player. Optional target_american guides odds mix (e.g. +5000).
    """
    leg_count = max(2, min(int(leg_count), 25))
    game_date = game_date or date.today()

    search = search_daily_props(
        game_date,
        bookmaker=bookmaker,
        actionable_only=True,
        scan=True,
        limit=500,
    )
    pool = search.get("props") or []
    candidates = [
        p
        for p in pool
        if prop_is_bettable(p) and p.get("recommended_odds") is not None
    ]
    candidates = _best_prop_per_player(candidates)

    if len(candidates) < leg_count:
        return {
            "status": "insufficient",
            "message": (
                f"Only {len(candidates)} bettable actionable props found "
                f"(need {leg_count}). Refresh lines or lower leg count."
            ),
            "pool_size": len(candidates),
            "games_scanned": search.get("games_scanned"),
            "hint": search.get("hint"),
        }

    if target_american is not None:
        selected = _select_for_target_odds(candidates, leg_count, int(target_american))
    else:
        candidates.sort(key=lambda p: (prop_rank_key(p), -_form_score(p)))
        selected = candidates[:leg_count]

    legs = [_leg_from_prop(p) for p in selected]
    eval_out = evaluate_prop_parlay(legs)
    target_delta = None
    if target_american is not None and eval_out.get("american_payout") is not None:
        target_delta = int(eval_out["american_payout"]) - int(target_american)

    return {
        "status": "ok",
        "leg_count": len(legs),
        "target_american": target_american,
        "target_delta": target_delta,
        "legs": legs,
        "props": selected,
        "eval": eval_out,
        "pool_size": len(candidates),
        "games_scanned": search.get("games_scanned"),
        "games_on_slate": search.get("games_on_slate"),
        "bookmaker": search.get("bookmaker"),
        "bookmaker_label": search.get("bookmaker_label"),
        "hint": search.get("hint"),
    }
