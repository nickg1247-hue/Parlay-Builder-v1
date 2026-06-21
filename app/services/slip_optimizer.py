"""Suggest one leg swap to improve a prop parlay slip."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.props_mlb import (
    evaluate_prop_parlay,
    prop_slip_leg,
    search_daily_props,
)


def _norm_player(name: str | None) -> str:
    return str(name or "").strip().lower()


def _leg_score(leg: dict[str, Any]) -> float:
    for key in ("score", "rank_score", "hit_rate", "recommended_hit_rate"):
        val = leg.get(key)
        if val is not None:
            try:
                rate = float(val)
                return rate * 100 if rate <= 1.0 else rate
            except (TypeError, ValueError):
                continue
    return 0.0


def _prop_to_slip_leg(prop: dict[str, Any]) -> dict[str, Any]:
    gid = str(prop.get("game_id") or "")
    return prop_slip_leg(
        prop,
        game_id=gid,
        matchup=prop.get("matchup"),
        bookmaker=str(prop.get("bookmaker") or "draftkings"),
        game_date=prop.get("game_date") or prop.get("date") or date.today().isoformat(),
    )


def _players_in_legs(legs: list[dict[str, Any]]) -> set[str]:
    return {_norm_player(l.get("player")) for l in legs if _norm_player(l.get("player"))}


def suggest_prop_slip_swap(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one swap that raises average form score while respecting one-leg-per-player."""
    if not legs:
        return {"status": "empty", "message": "Add legs to your slip first."}
    if len(legs) < 2:
        return {
            "status": "need_more_legs",
            "message": "Add at least two legs before requesting a swap suggestion.",
        }

    pool = search_daily_props(date.today(), actionable_only=True, limit=200).get("props") or []
    if not pool:
        return {"status": "no_pool", "message": "No actionable props available today to swap in."}

    scores = [_leg_score(l) for l in legs]
    weakest_i = min(range(len(scores)), key=lambda i: scores[i])
    weakest = legs[weakest_i]
    weakest_player = _norm_player(weakest.get("player"))
    used_players = _players_in_legs(legs)

    best_leg: dict[str, Any] | None = None
    best_total = sum(scores)
    best_reason = ""

    for prop in pool:
        pname = _norm_player(prop.get("player"))
        if not pname:
            continue
        pscore = _leg_score(_prop_to_slip_leg(prop))
        if pscore <= scores[weakest_i]:
            continue

        candidate = _prop_to_slip_leg(prop)
        new_players = used_players - {weakest_player}
        if pname in new_players:
            continue

        new_total = sum(scores) - scores[weakest_i] + pscore
        if new_total <= best_total:
            continue

        hr = prop.get("recommended_hit_rate")
        hr_txt = f"{round(float(hr) * 100)}% L10" if hr is not None else "stronger form"
        if pname == weakest_player:
            reason = (
                f"Same player, better line: {prop.get('market_label')} "
                f"{prop.get('recommended_side')} {prop.get('line')} ({hr_txt})"
            )
        else:
            reason = (
                f"Replace {weakest.get('player')} with {prop.get('player')} — "
                f"{prop.get('market_label')} ({hr_txt})"
            )

        best_leg = candidate
        best_total = new_total
        best_reason = reason

    if not best_leg:
        return {
            "status": "no_improvement",
            "message": "No stronger actionable props found that fit your slip rules.",
            "weakest_index": weakest_i,
            "weakest_leg": weakest,
        }

    new_legs = list(legs)
    new_legs[weakest_i] = best_leg
    return {
        "status": "ok",
        "swap_index": weakest_i,
        "replace_leg": weakest,
        "suggested_leg": best_leg,
        "reason": best_reason,
        "score_before": round(sum(scores), 2),
        "score_after": round(best_total, 2),
        "new_eval": evaluate_prop_parlay(new_legs),
        "new_legs": new_legs,
    }
