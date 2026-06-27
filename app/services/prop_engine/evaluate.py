"""Main prop evaluation: both sides scored, best side recommended."""

from __future__ import annotations

from typing import Any

from app.odds.odds_math import american_to_implied_prob, market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds
from app.services.prop_engine.components import (
    compute_side_edge,
    market_edge_score_from_edge,
    score_consistency,
    score_context,
    score_line_value,
    score_matchup,
    score_recent_form,
    score_role_usage,
)
from app.services.prop_engine.constants import (
    ELITE_EDGE,
    ELITE_LINE_VALUE,
    ELITE_MATCHUP,
    ELITE_ROLE,
    ELITE_SCORE,
    MIN_EDGE,
    MIN_GAMES_FOR_SCORE,
    MIN_PROP_SCORE,
    SCORE_WEIGHTS,
    VERY_STRONG_EDGE,
    VERY_STRONG_MATCHUP,
    VERY_STRONG_ROLE,
    VERY_STRONG_SCORE,
)
from app.services.prop_engine.probabilities import model_probabilities
from app.services.prop_engine.projections import build_projection, projection_supports_side
from app.services.prop_engine.utils import recent_game_window
from app.services.prop_scoring import (
    MARKET_STAT,
    _alltime_game_log_values,
    _hit_rates,
    _matchup_adjustment,
    _search_player_id,
    _season_game_log_values,
    market_label,
    prop_grade_from_score,
)
from app.services.teams_hub import _mlb_player_photo


def _weighted_total(scores: dict[str, float]) -> float:
    total = sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    return round(max(0.0, min(100.0, total)), 1)


def _risk_flag(
    *,
    consistency_meta: dict[str, Any],
    role_meta: dict[str, Any],
    projection_confidence: str,
    edge: float | None,
) -> str:
    vol = consistency_meta.get("volatility", "medium")
    role_flags = role_meta.get("role_flags") or []
    if vol == "high" or role_flags or projection_confidence == "low":
        return "High Risk"
    if vol == "medium" or (edge is not None and edge < 0.08):
        return "Medium Risk"
    return "Low Risk"


def _confidence_tier(
    *,
    prop_score: float,
    edge: float | None,
    role_score: float,
    matchup_score: float,
    line_value_score: float,
    role_flags: list[str],
) -> str:
    if (
        prop_score >= ELITE_SCORE
        and edge is not None
        and edge >= ELITE_EDGE
        and role_score >= ELITE_ROLE
        and matchup_score >= ELITE_MATCHUP
        and line_value_score >= ELITE_LINE_VALUE
        and not role_flags
    ):
        return "elite"
    if (
        prop_score >= VERY_STRONG_SCORE
        and edge is not None
        and edge >= VERY_STRONG_EDGE
        and role_score >= VERY_STRONG_ROLE
        and matchup_score >= VERY_STRONG_MATCHUP
    ):
        return "very_strong"
    if prop_score >= MIN_PROP_SCORE:
        return "strong"
    return "rejected"


def _line_strength_from_tier(tier: str) -> tuple[str, str]:
    mapping = {
        "elite": ("elite", "Elite"),
        "very_strong": ("very_strong", "Very strong"),
        "strong": ("strong", "Strong line"),
        "rejected": ("weak", "Weak line"),
    }
    key, label = mapping.get(tier, ("weak", "Weak line"))
    return key, label


def _score_side(
    side: str,
    *,
    line: float,
    values: list[float],
    l5_over: float | None,
    l5_under: float | None,
    l10_over: float | None,
    l10_under: float | None,
    season_over: float | None,
    season_under: float | None,
    projection: float,
    median: float | None,
    std_dev: float | None,
    model_prob: float,
    market_prob: float | None,
    market_type: str,
    opposing_pitcher_era: float | None,
    pitcher_k_rate: float | None,
    matchup_notes: list[str],
    sample_games: int,
) -> dict[str, Any]:
    l5 = l5_over if side == "over" else l5_under
    l10 = l10_over if side == "over" else l10_under
    season = season_over if side == "over" else season_under

    form_score, form_meta = score_recent_form(
        side=side,
        l5_rate=l5,
        l10_rate=l10,
        season_rate=season,
        values=values,
        line=line,
    )
    matchup_score, matchup_meta = score_matchup(
        market_type=market_type,
        opposing_pitcher_era=opposing_pitcher_era,
        pitcher_k_rate=pitcher_k_rate,
        matchup_notes=matchup_notes,
    )
    role_score, role_meta = score_role_usage(sample_games=sample_games, market_type=market_type)
    line_value_score, line_meta = score_line_value(
        side=side,
        projection=projection,
        median=median,
        line=line,
        std_dev=std_dev,
    )
    edge = compute_side_edge(model_prob, market_prob)
    edge_score = market_edge_score_from_edge(edge)
    consistency_score, consistency_meta = score_consistency(values=values, line=line, side=side)
    context_score, context_meta = score_context()

    component_scores = {
        "recent_form": form_score,
        "matchup": matchup_score,
        "role_usage": role_score,
        "line_value": line_value_score,
        "market_edge": edge_score,
        "consistency": consistency_score,
        "context": context_score,
    }
    prop_score = _weighted_total(component_scores)

    return {
        "side": side,
        "prop_score": prop_score,
        "component_scores": component_scores,
        "model_probability": model_prob,
        "market_probability": market_prob,
        "edge": edge,
        "edge_pct": round(edge * 100, 2) if edge is not None else None,
        "form_meta": form_meta,
        "matchup_meta": matchup_meta,
        "role_meta": role_meta,
        "line_meta": line_meta,
        "consistency_meta": consistency_meta,
        "context_meta": context_meta,
        "projection_agrees": projection_supports_side(projection, line, side),
    }


def _best_reason(side_eval: dict[str, Any], projection: float, line: float) -> str:
    parts: list[str] = []
    edge = side_eval.get("edge_pct")
    if edge is not None and edge >= 5:
        parts.append(f"Edge {edge:.1f}%")
    comps = side_eval.get("component_scores") or {}
    if comps.get("line_value", 0) >= 75:
        parts.append(f"Proj {projection:.1f} vs line {line:g}")
    if comps.get("matchup", 0) >= 75:
        parts.append("Favorable matchup")
    if comps.get("recent_form", 0) >= 70:
        parts.append("Recent form supports side")
    return " · ".join(parts[:3]) if parts else "Quant model alignment"


def _rejection_reasons(
    over_eval: dict[str, Any],
    under_eval: dict[str, Any],
    *,
    over_odds: int | None,
    under_odds: int | None,
    projection: float | None,
    line: float,
) -> list[str]:
    reasons: list[str] = []
    candidates = []
    if over_odds is not None:
        candidates.append(over_eval)
    if under_odds is not None:
        candidates.append(under_eval)

    if not candidates:
        reasons.append("No sportsbook odds")
        return reasons

    best = max(candidates, key=lambda x: x["prop_score"])
    if best["prop_score"] < MIN_PROP_SCORE:
        reasons.append(f"Prop score {best['prop_score']:.0f} below {MIN_PROP_SCORE:.0f} threshold")
    if best.get("edge") is None or best["edge"] < MIN_EDGE:
        reasons.append("Edge below threshold")
    if not best.get("projection_agrees"):
        reasons.append("Projection disagrees with side")
    if best["role_meta"].get("role_flags"):
        reasons.extend(best["role_meta"]["role_flags"])
    if best["consistency_meta"].get("volatility") == "high":
        reasons.append("High volatility")
    return reasons or ["Did not meet recommendation filters"]


def evaluate_prop(
    *,
    player: str,
    market_type: str,
    line: float,
    over_odds: int | None,
    under_odds: int | None,
    season: int,
    opposing_pitcher: str | None = None,
    opposing_pitcher_era: float | None = None,
    sport: str = "mlb",
    game_id: str | None = None,
    team: str | None = None,
    opponent: str | None = None,
) -> dict[str, Any]:
    """Evaluate both sides and return a scored prop with recommendation metadata."""
    empty = _empty_result(market_type)

    mapping = MARKET_STAT.get(market_type)
    if mapping is None:
        return {**empty, "rejection_reasons": ["Unknown market type"]}

    player_id = _search_player_id(player)
    if player_id is None:
        return {**empty, "rejection_reasons": ["Could not match player to MLB stats"]}

    group, stat_key = mapping
    values = list(_season_game_log_values(player_id, group, stat_key, season))
    if len(values) < MIN_GAMES_FOR_SCORE:
        return {
            **empty,
            "sample_games_season": len(values),
            "rejection_reasons": [f"Only {len(values)} games logged this season"],
        }

    l5 = recent_game_window(values, 5)
    l10 = recent_game_window(values, 10)
    l5_over, l5_under = _hit_rates(l5, line)
    l10_over, l10_under = _hit_rates(l10, line)
    season_over, season_under = _hit_rates(values, line)
    alltime_values = list(_alltime_game_log_values(player_id, group, stat_key, season))
    alltime_over, alltime_under = _hit_rates(alltime_values, line)

    era = opposing_pitcher_era
    if era is None and opposing_pitcher:
        from app.data.pitcher_lookup import lookup_pitcher_rates

        era, _fip = lookup_pitcher_rates(opposing_pitcher, season, {})

    recent_avg = sum(l10) / len(l10) if l10 else 0.0
    k_rate = recent_avg if market_type == "pitcher_strikeouts" else None
    adj, matchup_notes = _matchup_adjustment(
        market_type,
        opposing_pitcher_era=era,
        pitcher_k_rate=k_rate,
    )

    proj = build_projection(
        values,
        market_type=market_type,
        opposing_pitcher_era=era,
        matchup_adjustment=adj,
    )
    projection = proj["model_projection"] or recent_avg
    std_dev = proj.get("std_dev")
    median = proj.get("median_outcome")

    probs = model_probabilities(
        line,
        projection=projection,
        std_dev=std_dev,
        empirical_values=values,
    )

    over_mkt = under_mkt = None
    over_valid = over_odds is not None and is_valid_american_odds(over_odds)
    under_valid = under_odds is not None and is_valid_american_odds(under_odds)
    if over_valid and under_valid:
        over_mkt, under_mkt = market_probs_from_american_totals(int(over_odds), int(under_odds))
    elif over_valid:
        over_mkt = american_to_implied_prob(int(over_odds))
    elif under_valid:
        under_mkt = american_to_implied_prob(int(under_odds))

    over_eval = _score_side(
        "over",
        line=line,
        values=values,
        l5_over=l5_over,
        l5_under=l5_under,
        l10_over=l10_over,
        l10_under=l10_under,
        season_over=season_over,
        season_under=season_under,
        projection=projection,
        median=median,
        std_dev=std_dev,
        model_prob=probs["model_probability_over"],
        market_prob=over_mkt,
        market_type=market_type,
        opposing_pitcher_era=era,
        pitcher_k_rate=k_rate,
        matchup_notes=matchup_notes,
        sample_games=len(values),
    )
    under_eval = _score_side(
        "under",
        line=line,
        values=values,
        l5_over=l5_over,
        l5_under=l5_under,
        l10_over=l10_over,
        l10_under=l10_under,
        season_over=season_over,
        season_under=season_under,
        projection=projection,
        median=median,
        std_dev=std_dev,
        model_prob=probs["model_probability_under"],
        market_prob=under_mkt,
        market_type=market_type,
        opposing_pitcher_era=era,
        pitcher_k_rate=k_rate,
        matchup_notes=matchup_notes,
        sample_games=len(values),
    )

    over_edge = compute_side_edge(probs["model_probability_over"], over_mkt)
    under_edge = compute_side_edge(probs["model_probability_under"], under_mkt)

    trap_reason: str | None = None
    if over_odds is not None and under_odds is None and l10_under is not None and l10_over is not None:
        if l10_under >= 0.70 and l10_under - (l10_over or 0) >= 0.20:
            trap_reason = f"Under hits {l10_under:.0%} L10 but only Over is listed"
    if under_odds is not None and over_odds is None and l10_over is not None and l10_under is not None:
        if l10_over >= 0.70 and l10_over - (l10_under or 0) >= 0.20:
            trap_reason = f"Over hits {l10_over:.0%} L10 but only Under is listed"

    offered: list[dict[str, Any]] = []
    if over_odds is not None:
        offered.append({**over_eval, "odds": over_odds, "edge": over_edge})
    if under_odds is not None:
        offered.append({**under_eval, "odds": under_odds, "edge": under_edge})

    eligible = [
        s
        for s in offered
        if s["prop_score"] >= MIN_PROP_SCORE
        and s.get("edge") is not None
        and s["edge"] >= MIN_EDGE
        and s.get("projection_agrees")
    ]

    rejection_reasons = _rejection_reasons(
        over_eval, under_eval, over_odds=over_odds, under_odds=under_odds, projection=projection, line=line
    )

    if trap_reason:
        eligible = []
        rejection_reasons = [trap_reason]

    recommended: dict[str, Any] | None = None
    if eligible:
        recommended = max(eligible, key=lambda s: (s["prop_score"], s.get("edge") or 0))
    elif trap_reason and offered:
        recommended = max(offered, key=lambda s: s.get("prop_score", 0))

    side = recommended["side"] if recommended else None
    recommended_odds = recommended["odds"] if recommended else None
    recommended_hit_rate = (
        l10_over if side == "over" else l10_under if side == "under" else None
    )

    tier = "rejected"
    actionable = False
    actionable_reason = rejection_reasons[0] if rejection_reasons else None

    if recommended and eligible:
        tier = _confidence_tier(
            prop_score=recommended["prop_score"],
            edge=recommended.get("edge"),
            role_score=recommended["component_scores"]["role_usage"],
            matchup_score=recommended["component_scores"]["matchup"],
            line_value_score=recommended["component_scores"]["line_value"],
            role_flags=recommended["role_meta"].get("role_flags") or [],
        )
        if tier != "rejected":
            actionable = True
            actionable_reason = None
            rejection_reasons = []

    prop_score = recommended["prop_score"] if recommended else max(
        over_eval["prop_score"], under_eval["prop_score"]
    )
    grade_tier, grade_label = prop_grade_from_score(prop_score)
    line_strength, line_strength_label = grade_tier, grade_label
    best_reason = _best_reason(recommended or over_eval, projection, line) if recommended else None

    side_eval = recommended or (over_eval if over_odds else under_eval)
    risk_flag = _risk_flag(
        consistency_meta=side_eval.get("consistency_meta") or {},
        role_meta=side_eval.get("role_meta") or {},
        projection_confidence=proj.get("projection_confidence", "low"),
        edge=side_eval.get("edge"),
    )

    line_insight = best_reason or (rejection_reasons[0] if rejection_reasons else "Not recommended")
    if recommended and recommended_hit_rate is not None:
        line_insight = f"{best_reason} · L10 {recommended_hit_rate:.0%}"

    debug = {
        "over": _debug_side(over_eval, over_edge),
        "under": _debug_side(under_eval, under_edge),
        "projection": proj,
        "rejection_reasons": rejection_reasons,
        "recommendation": side,
    }

    return {
        "player_name": player,
        "sport": sport,
        "game_id": game_id,
        "team": team,
        "opponent": opponent,
        "stat_type": market_type,
        "line": line,
        "sportsbook_odds": {"over": over_odds, "under": under_odds},
        "model_projection": projection,
        "model_probability_over": probs["model_probability_over"],
        "model_probability_under": probs["model_probability_under"],
        "market_probability_over": round(over_mkt, 4) if over_mkt is not None else None,
        "market_probability_under": round(under_mkt, 4) if under_mkt is not None else None,
        "over_edge": over_edge,
        "under_edge": under_edge,
        "prop_score_over": over_eval["prop_score"],
        "prop_score_under": under_eval["prop_score"],
        "recommended_side": side,
        "recommended_probability": (
            probs["model_probability_over"] if side == "over"
            else probs["model_probability_under"] if side == "under"
            else None
        ),
        "confidence": tier,
        "confidence_tier": tier,
        "prop_score": prop_score,
        "score": prop_score if actionable else None,
        "rank_score": prop_score if actionable else None,
        "component_scores": side_eval.get("component_scores"),
        "recent_form_grade": side_eval["component_scores"]["recent_form"] if side_eval else None,
        "matchup_grade": side_eval["component_scores"]["matchup"] if side_eval else None,
        "line_value_grade": side_eval["component_scores"]["line_value"] if side_eval else None,
        "edge_pct": side_eval.get("edge_pct"),
        "risk_flag": risk_flag,
        "best_reason": best_reason,
        "rejection_reasons": rejection_reasons,
        "debug": debug,
        # backward-compatible fields
        "player_id": player_id,
        "photo_url": _mlb_player_photo(player_id),
        "recommended_odds": recommended_odds,
        "recommended_hit_rate": recommended_hit_rate,
        "actionable": actionable,
        "actionable_reason": actionable_reason,
        "hit_rate_over_l5": l5_over,
        "hit_rate_under_l5": l5_under,
        "hit_rate_over_l10": l10_over,
        "hit_rate_under_l10": l10_under,
        "hit_rate_over_season": season_over,
        "hit_rate_under_season": season_under,
        "hit_rate_over_alltime": alltime_over,
        "hit_rate_under_alltime": alltime_under,
        "hit_rate_over": l10_over,
        "hit_rate_under": l10_under,
        "recent_avg": round(recent_avg, 2),
        "recent_avg_l10": round(recent_avg, 2),
        "sample_games": len(l10),
        "sample_games_season": len(values),
        "factors": [best_reason] if best_reason else rejection_reasons[:2],
        "market_label": market_label(market_type),
        "line_strength": line_strength,
        "line_strength_label": line_strength_label,
        "grade_tier": grade_tier,
        "grade_label": grade_label,
        "line_insight": line_insight,
        "matchup_adjustment": round(adj, 1) if adj else None,
    }


def _debug_side(side_eval: dict[str, Any], edge: float | None) -> dict[str, Any]:
    return {
        "prop_score": side_eval["prop_score"],
        "component_scores": side_eval["component_scores"],
        "model_probability": side_eval["model_probability"],
        "market_probability": side_eval["market_probability"],
        "edge": edge,
        "projection_agrees": side_eval["projection_agrees"],
    }


def _empty_result(market_type: str) -> dict[str, Any]:
    return {
        "score": None,
        "prop_score": None,
        "rank_score": None,
        "recommended_side": None,
        "recommended_odds": None,
        "recommended_hit_rate": None,
        "recommended_probability": None,
        "actionable": False,
        "actionable_reason": None,
        "confidence": "rejected",
        "confidence_tier": "rejected",
        "hit_rate_over_l5": None,
        "hit_rate_under_l5": None,
        "hit_rate_over_l10": None,
        "hit_rate_under_l10": None,
        "hit_rate_over_season": None,
        "hit_rate_under_season": None,
        "recent_avg_l10": None,
        "sample_games_season": 0,
        "factors": [],
        "market_label": market_label(market_type),
        "line_strength": None,
        "line_strength_label": None,
        "line_insight": None,
        "matchup_adjustment": None,
        "rejection_reasons": [],
        "debug": {},
    }
