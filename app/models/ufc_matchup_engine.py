"""UFC matchup-based prediction engine — style-aware, not record-only.

Scores fighters on striking, grappling, cardio, IQ, form, and physical factors,
then applies matchup-specific adjustments before predicting winner, confidence,
method probabilities, reasons, and risks.

Proxy features are derived from fight history, records, weight class, layoff, and
Elo until per-bout striking/grappling stats are ingested.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from app.features.ufc_pregame import (
    DEFAULT_REST_FILL,
    NEUTRAL_WIN_PCT,
    build_fighter_tracker_from_history,
    fighter_layoff_days,
)
from app.odds.ufc_fighter_aliases import normalize_fighter_name

# Category weights (must sum to 1.0)
CATEGORY_WEIGHTS: dict[str, float] = {
    "style_matchup": 0.20,
    "striking": 0.15,
    "wrestling_grappling": 0.15,
    "cardio_pace": 0.10,
    "fight_iq": 0.08,
    "recent_form": 0.08,
    "physical_advantages": 0.07,
    "strength_of_competition": 0.07,
    "age_career_trend": 0.05,
    "miscellaneous": 0.05,
}

FEATURE_SCORE_FIELDS: tuple[str, ...] = (
    "striking_score",
    "striking_defense_score",
    "damage_differential",
    "knockout_power_score",
    "takedown_offense_score",
    "takedown_defense_score",
    "control_time_score",
    "submission_threat_score",
    "getup_score",
    "cardio_score",
    "late_round_output_score",
    "fight_iq_score",
    "recent_form_score",
    "strength_of_schedule_score",
    "age_curve_score",
    "durability_score",
    "reach_advantage_score",
    "pace_score",
    "finish_threat_score",
    "momentum_score",
)

ADJUSTED_ADVANTAGE_FIELDS: tuple[str, ...] = (
    "adjusted_striking_advantage",
    "adjusted_grappling_advantage",
    "adjusted_cardio_advantage",
    "adjusted_physical_advantage",
    "adjusted_style_advantage",
    "adjusted_durability_advantage",
    "adjusted_strength_of_schedule_advantage",
)

_RECORD_RE = re.compile(
    r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?",
    re.IGNORECASE,
)

_WEIGHT_CLASS_PRIORS: dict[str, dict[str, float]] = {
    "heavyweight": {
        "knockout_power_score": 82,
        "striking_score": 72,
        "cardio_score": 58,
        "pace_score": 55,
        "takedown_offense_score": 48,
        "submission_threat_score": 42,
        "reach_advantage_score": 68,
    },
    "light heavyweight": {
        "knockout_power_score": 78,
        "striking_score": 74,
        "cardio_score": 62,
        "pace_score": 60,
        "takedown_offense_score": 52,
        "submission_threat_score": 48,
    },
    "middleweight": {
        "knockout_power_score": 72,
        "striking_score": 76,
        "cardio_score": 68,
        "pace_score": 66,
        "takedown_offense_score": 58,
        "submission_threat_score": 52,
    },
    "welterweight": {
        "knockout_power_score": 68,
        "striking_score": 78,
        "cardio_score": 72,
        "pace_score": 70,
        "takedown_offense_score": 62,
        "submission_threat_score": 55,
    },
    "lightweight": {
        "knockout_power_score": 62,
        "striking_score": 80,
        "cardio_score": 78,
        "pace_score": 76,
        "takedown_offense_score": 65,
        "submission_threat_score": 58,
    },
    "featherweight": {
        "knockout_power_score": 58,
        "striking_score": 82,
        "cardio_score": 82,
        "pace_score": 80,
        "takedown_offense_score": 68,
        "submission_threat_score": 60,
    },
    "bantamweight": {
        "knockout_power_score": 52,
        "striking_score": 84,
        "cardio_score": 85,
        "pace_score": 82,
        "takedown_offense_score": 70,
        "submission_threat_score": 62,
    },
    "flyweight": {
        "knockout_power_score": 48,
        "striking_score": 86,
        "cardio_score": 88,
        "pace_score": 84,
        "takedown_offense_score": 72,
        "submission_threat_score": 64,
    },
    "women": {
        "knockout_power_score": 55,
        "striking_score": 78,
        "cardio_score": 80,
        "pace_score": 76,
        "takedown_offense_score": 66,
        "submission_threat_score": 58,
    },
    "default": {
        "knockout_power_score": 65,
        "striking_score": 75,
        "cardio_score": 70,
        "pace_score": 68,
        "takedown_offense_score": 60,
        "submission_threat_score": 55,
        "reach_advantage_score": 60,
    },
}


@dataclass
class ParsedRecord:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    total_bouts: int = 0

    @property
    def win_pct(self) -> float:
        if self.total_bouts <= 0:
            return NEUTRAL_WIN_PCT
        return self.wins / self.total_bouts


@dataclass
class FighterContext:
    name: str
    side: str  # "away" | "home"
    record: ParsedRecord
    career_win_pct: float = NEUTRAL_WIN_PCT
    last5_win_pct: float = NEUTRAL_WIN_PCT
    layoff_days: int | None = None
    rest_days: float = DEFAULT_REST_FILL
    b2b: int = 0
    elo_pre: float = 1500.0
    weight_class: str = ""
    short_notice: bool = False
    fights_per_year: float = 2.0
    career_span_years: float = 5.0
    opponent_win_pct_avg: float = NEUTRAL_WIN_PCT
    recent_losses: int = 0
    finish_rate_proxy: float = 0.35


@dataclass
class FighterFeatureScores:
    striking_score: float = 50.0
    striking_defense_score: float = 50.0
    damage_differential: float = 50.0
    knockout_power_score: float = 50.0
    takedown_offense_score: float = 50.0
    takedown_defense_score: float = 50.0
    control_time_score: float = 50.0
    submission_threat_score: float = 50.0
    getup_score: float = 50.0
    cardio_score: float = 50.0
    late_round_output_score: float = 50.0
    fight_iq_score: float = 50.0
    recent_form_score: float = 50.0
    strength_of_schedule_score: float = 50.0
    age_curve_score: float = 50.0
    durability_score: float = 50.0
    reach_advantage_score: float = 50.0
    pace_score: float = 50.0
    finish_threat_score: float = 50.0
    momentum_score: float = 50.0
    style_archetype: str = "balanced"

    def as_dict(self) -> dict[str, float | str]:
        d = {k: round(getattr(self, k), 1) for k in FEATURE_SCORE_FIELDS}
        d["style_archetype"] = self.style_archetype
        return d

    def composite(self) -> float:
        return sum(getattr(self, k) for k in FEATURE_SCORE_FIELDS) / len(FEATURE_SCORE_FIELDS)


@dataclass
class MatchupAdjustments:
    adjusted_striking_advantage: float = 0.0
    adjusted_grappling_advantage: float = 0.0
    adjusted_cardio_advantage: float = 0.0
    adjusted_physical_advantage: float = 0.0
    adjusted_style_advantage: float = 0.0
    adjusted_durability_advantage: float = 0.0
    adjusted_strength_of_schedule_advantage: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {k: round(getattr(self, k), 2) for k in ADJUSTED_ADVANTAGE_FIELDS}


def parse_record(record: str | None) -> ParsedRecord:
    if not record:
        return ParsedRecord()
    m = _RECORD_RE.search(str(record))
    if not m:
        return ParsedRecord()
    wins = int(m.group(1))
    losses = int(m.group(2))
    draws = int(m.group(3) or 0)
    return ParsedRecord(
        wins=wins,
        losses=losses,
        draws=draws,
        total_bouts=wins + losses + draws,
    )


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _normalize_weight_class(weight_class: str | None) -> str:
    wc = str(weight_class or "").lower().replace(" bout", "").strip()
    if "women" in wc or "w " in wc or wc.startswith("w "):
        return "women"
    for key in _WEIGHT_CLASS_PRIORS:
        if key == "default":
            continue
        if key in wc:
            return key
    return "default"


def _prior_for_weight(weight_class: str | None) -> dict[str, float]:
    key = _normalize_weight_class(weight_class)
    base = dict(_WEIGHT_CLASS_PRIORS["default"])
    base.update(_WEIGHT_CLASS_PRIORS.get(key, {}))
    return base


def _infer_archetype(scores: FighterFeatureScores) -> str:
    grap = (scores.takedown_offense_score + scores.submission_threat_score) / 2
    strike = (scores.striking_score + scores.knockout_power_score) / 2
    if grap >= strike + 12 and scores.submission_threat_score >= 62:
        return "grappler"
    if grap >= strike + 8 and scores.takedown_offense_score >= 65:
        return "wrestler"
    if scores.pace_score >= 72 and scores.cardio_score >= 70:
        return "pressure"
    if scores.striking_defense_score >= 68 and strike >= 65:
        return "counter_striker"
    if strike >= grap + 10 and scores.knockout_power_score >= 68:
        return "striker"
    return "balanced"


def _scale_from_rate(rate: float, *, low: float = 0.35, high: float = 0.75) -> float:
    if rate <= low:
        return 35.0 + (rate / max(low, 0.01)) * 20.0
    if rate >= high:
        return 80.0 + min(20.0, (rate - high) / max(1.0 - high, 0.01) * 20.0)
    return 55.0 + ((rate - low) / (high - low)) * 25.0


def build_fighter_context(
    *,
    name: str,
    side: str,
    fight: dict[str, Any],
    slate_day: date,
    feature_row: dict[str, Any] | None = None,
    history_df: pd.DataFrame | None = None,
) -> FighterContext:
    from app.models.ufc_baseline import load_fights

    prefix = side
    record = parse_record(fight.get(f"{prefix}_record"))
    feat = feature_row or {}
    layoff = fighter_layoff_days(name, slate_day)

    career_win_pct = float(feat.get(f"{prefix}_career_win_pct") or record.win_pct)
    last5 = float(feat.get(f"{prefix}_last5_win_pct") or NEUTRAL_WIN_PCT)
    rest = float(feat.get(f"{prefix}_rest_days") or DEFAULT_REST_FILL)
    b2b = int(feat.get(f"{prefix}_b2b") or 0)
    elo = float(feat.get(f"elo_{prefix}_pre") or 1500.0)

    short_notice = rest < 21 and (layoff is None or layoff > 120)

    norm = normalize_fighter_name(name)
    fights = history_df if history_df is not None else load_fights()
    fights = fights[fights["home_win"].notna()].copy()
    fights["date"] = pd.to_datetime(fights["date"])
    as_of = pd.Timestamp(slate_day)

    fighter_fights = fights[
        (fights["home_team"].map(normalize_fighter_name) == norm)
        | (fights["away_team"].map(normalize_fighter_name) == norm)
    ]
    fighter_fights = fighter_fights[fighter_fights["date"] < as_of].sort_values("date")

    career_span = 5.0
    fights_per_year = 2.0
    if not fighter_fights.empty:
        span_days = max(1, (as_of - fighter_fights["date"].min()).days)
        career_span = max(1.0, span_days / 365.25)
        fights_per_year = len(fighter_fights) / career_span

    tracker = build_fighter_tracker_from_history(fights[fights["date"] < as_of])
    prior = tracker.fights_before(norm, as_of)
    recent_losses = sum(1 for g in prior[-5:] if g.win == 0)

    opp_win_pcts: list[float] = []
    for row in fighter_fights.itertuples(index=False):
        opp = (
            normalize_fighter_name(str(row.away_team))
            if normalize_fighter_name(str(row.home_team)) == norm
            else normalize_fighter_name(str(row.home_team))
        )
        opp_prior = tracker.fights_before(opp, pd.to_datetime(row.date))
        if opp_prior:
            opp_win_pcts.append(sum(g.win for g in opp_prior) / len(opp_prior))
    opp_avg = sum(opp_win_pcts) / len(opp_win_pcts) if opp_win_pcts else NEUTRAL_WIN_PCT

    finish_proxy = min(0.85, 0.25 + (record.wins / max(record.total_bouts, 1)) * 0.35)
    if last5 >= 0.8:
        finish_proxy += 0.08
    if recent_losses >= 3:
        finish_proxy -= 0.1

    return FighterContext(
        name=name,
        side=side,
        record=record,
        career_win_pct=career_win_pct,
        last5_win_pct=last5,
        layoff_days=layoff,
        rest_days=rest,
        b2b=b2b,
        elo_pre=elo,
        weight_class=str(fight.get("weight_class") or ""),
        short_notice=short_notice,
        fights_per_year=fights_per_year,
        career_span_years=career_span,
        opponent_win_pct_avg=opp_avg,
        recent_losses=recent_losses,
        finish_rate_proxy=finish_proxy,
    )


def score_fighter(ctx: FighterContext) -> FighterFeatureScores:
    """Build all per-fighter feature scores (0–100) from context."""
    priors = _prior_for_weight(ctx.weight_class)
    rec = ctx.record

    striking = _scale_from_rate(ctx.last5_win_pct * 0.55 + ctx.career_win_pct * 0.45)
    striking = _clamp(striking * 0.65 + priors.get("striking_score", 75) * 0.35)

    ko_power = _clamp(
        priors.get("knockout_power_score", 65) * 0.5
        + ctx.finish_rate_proxy * 55
        + (ctx.career_win_pct - 0.5) * 30
    )
    strike_def = _clamp(52 + (ctx.career_win_pct - 0.5) * 40 - ctx.recent_losses * 4)

    td_off = _clamp(
        priors.get("takedown_offense_score", 60) * 0.45
        + ctx.career_win_pct * 35
        + min(15, ctx.fights_per_year * 4)
    )
    td_def = _clamp(50 + (ctx.career_win_pct - 0.5) * 38 - ctx.recent_losses * 3)
    control = _clamp(td_off * 0.55 + ctx.career_win_pct * 30 + 15)
    sub_threat = _clamp(
        priors.get("submission_threat_score", 55) * 0.5
        + td_off * 0.25
        + (ctx.career_win_pct - 0.45) * 25
    )
    getup = _clamp(55 + strike_def * 0.25 - td_off * 0.1)

    cardio = _clamp(
        priors.get("cardio_score", 70) * 0.4
        + (1.0 - min(1.0, max(0, ctx.layoff_days or 90) / 500)) * 25
        + min(20, ctx.fights_per_year * 5)
        - ctx.b2b * 12
        - (15 if ctx.short_notice else 0)
    )
    late_round = _clamp(cardio * 0.6 + ctx.last5_win_pct * 35 + strike_def * 0.1)
    pace = _clamp(priors.get("pace_score", 68) * 0.45 + ctx.fights_per_year * 8 + striking * 0.2)

    iq = _clamp(
        48
        + (ctx.elo_pre - 1500) / 12
        + (ctx.career_win_pct - 0.5) * 22
        + min(12, rec.total_bouts / 4)
    )
    form = _clamp(ctx.last5_win_pct * 100)
    sos = _clamp(40 + (ctx.opponent_win_pct_avg - 0.5) * 80 + (ctx.elo_pre - 1500) / 20)

    est_age = 24 + min(14, rec.total_bouts / 2.5) + max(0, ctx.career_span_years - 4)
    age_curve = 72.0
    wc_key = _normalize_weight_class(ctx.weight_class)
    if est_age > 35 and wc_key in ("lightweight", "featherweight", "bantamweight", "flyweight", "women"):
        age_curve -= (est_age - 35) * 2.5
    elif est_age > 38:
        age_curve -= (est_age - 38) * 2.0
    elif est_age < 26:
        age_curve += (26 - est_age) * 0.8
    age_curve = _clamp(age_curve)

    durability = _clamp(
        62 + strike_def * 0.2 - ctx.recent_losses * 8 - rec.losses / max(rec.total_bouts, 1) * 25
    )
    reach = _clamp(priors.get("reach_advantage_score", 60) + (ctx.elo_pre - 1500) / 40)
    damage_diff = _clamp(striking * 0.35 + ko_power * 0.35 + strike_def * 0.3)
    finish_threat = _clamp(ko_power * 0.45 + sub_threat * 0.35 + pace * 0.2)
    momentum = _clamp(form * 0.7 + (ctx.last5_win_pct - ctx.career_win_pct) * 60 + 15)

    scores = FighterFeatureScores(
        striking_score=striking,
        striking_defense_score=strike_def,
        damage_differential=damage_diff,
        knockout_power_score=ko_power,
        takedown_offense_score=td_off,
        takedown_defense_score=td_def,
        control_time_score=control,
        submission_threat_score=sub_threat,
        getup_score=getup,
        cardio_score=cardio,
        late_round_output_score=late_round,
        fight_iq_score=iq,
        recent_form_score=form,
        strength_of_schedule_score=sos,
        age_curve_score=age_curve,
        durability_score=durability,
        reach_advantage_score=reach,
        pace_score=pace,
        finish_threat_score=finish_threat,
        momentum_score=momentum,
    )
    scores.style_archetype = _infer_archetype(scores)
    return scores


def _edge(a: float, b: float, scale: float = 50.0) -> float:
    """Positive = first fighter (away) advantage on -1..+1 scale."""
    return _clamp((a - b) / scale, -1.0, 1.0)


def compute_matchup_adjustments(
    away: FighterFeatureScores,
    home: FighterFeatureScores,
    away_ctx: FighterContext,
    home_ctx: FighterContext,
) -> MatchupAdjustments:
    """Matchup-specific deltas (positive favors away / fighterA)."""
    adj = MatchupAdjustments()

    adj.adjusted_striking_advantage = _edge(
        away.striking_score * 0.4
        + away.knockout_power_score * 0.35
        + away.pace_score * 0.25,
        home.striking_score * 0.4
        + home.knockout_power_score * 0.35
        + home.pace_score * 0.25,
    )
    if away.pace_score >= home.pace_score + 10 and home.cardio_score < 58:
        adj.adjusted_striking_advantage = _clamp(
            adj.adjusted_striking_advantage + 0.18, -1, 1
        )
    if away.reach_advantage_score >= home.reach_advantage_score + 8 and home.striking_defense_score < 55:
        adj.adjusted_striking_advantage = _clamp(
            adj.adjusted_striking_advantage + 0.12, -1, 1
        )

    away_grap = (
        away.takedown_offense_score * 0.35
        + away.control_time_score * 0.25
        + away.submission_threat_score * 0.4
    )
    home_grap = (
        home.takedown_offense_score * 0.35
        + home.control_time_score * 0.25
        + home.submission_threat_score * 0.4
    )
    adj.adjusted_grappling_advantage = _edge(away_grap, home_grap)
    if away.takedown_offense_score >= 68 and home.takedown_defense_score < 52:
        adj.adjusted_grappling_advantage = _clamp(adj.adjusted_grappling_advantage + 0.22, -1, 1)
    if away.submission_threat_score >= 65 and home.takedown_defense_score < 50:
        adj.adjusted_grappling_advantage = _clamp(adj.adjusted_grappling_advantage + 0.15, -1, 1)
    if home.takedown_offense_score >= 68 and away.takedown_defense_score < 52:
        adj.adjusted_grappling_advantage = _clamp(adj.adjusted_grappling_advantage - 0.22, -1, 1)

    adj.adjusted_cardio_advantage = _edge(
        away.cardio_score * 0.55 + away.late_round_output_score * 0.45,
        home.cardio_score * 0.55 + home.late_round_output_score * 0.45,
    )
    if away.pace_score >= 72 and home.cardio_score < 55:
        adj.adjusted_cardio_advantage = _clamp(adj.adjusted_cardio_advantage + 0.2, -1, 1)

    adj.adjusted_physical_advantage = _edge(
        away.reach_advantage_score * 0.5 + away.knockout_power_score * 0.3 + away.age_curve_score * 0.2,
        home.reach_advantage_score * 0.5 + home.knockout_power_score * 0.3 + home.age_curve_score * 0.2,
    )

    style_edge = 0.0
    if away.style_archetype == "wrestler" and home.takedown_defense_score < 52:
        style_edge += 0.28
    if away.style_archetype == "grappler" and home.takedown_defense_score < 48:
        style_edge += 0.22
    if away.style_archetype == "pressure" and home.cardio_score < 56:
        style_edge += 0.18
    if away.style_archetype == "counter_striker" and home.style_archetype == "pressure":
        style_edge += 0.15
    if away.style_archetype == "striker" and home.pace_score < 58:
        style_edge += 0.1
    if home.style_archetype == "wrestler" and away.takedown_defense_score < 52:
        style_edge -= 0.28
    if home.style_archetype == "counter_striker" and away.style_archetype == "pressure":
        style_edge -= 0.15
    if away.durability_score >= home.durability_score + 12 and home.knockout_power_score < 55:
        style_edge += 0.1
    adj.adjusted_style_advantage = _clamp(style_edge, -1, 1)

    adj.adjusted_durability_advantage = _edge(away.durability_score, home.durability_score)
    adj.adjusted_strength_of_schedule_advantage = _edge(
        away.strength_of_schedule_score, home.strength_of_schedule_score
    )

    # Layoff / notice penalties applied as away-favoring when home is worse prepared
    if (home_ctx.layoff_days or 0) > 400:
        adj.adjusted_style_advantage = _clamp(adj.adjusted_style_advantage - 0.08, -1, 1)
    if (away_ctx.layoff_days or 0) > 400:
        adj.adjusted_style_advantage = _clamp(adj.adjusted_style_advantage + 0.08, -1, 1)

    return adj


def _category_edges(
    away: FighterFeatureScores,
    home: FighterFeatureScores,
    adj: MatchupAdjustments,
    away_ctx: FighterContext,
    home_ctx: FighterContext,
) -> dict[str, float]:
    """Per-category edge in [-1, +1], positive = away (fighterA) advantage."""
    return {
        "style_matchup": adj.adjusted_style_advantage,
        "striking": adj.adjusted_striking_advantage,
        "wrestling_grappling": adj.adjusted_grappling_advantage,
        "cardio_pace": adj.adjusted_cardio_advantage,
        "fight_iq": _edge(away.fight_iq_score, home.fight_iq_score),
        "recent_form": _edge(away.recent_form_score, home.recent_form_score),
        "physical_advantages": adj.adjusted_physical_advantage,
        "strength_of_competition": adj.adjusted_strength_of_schedule_advantage,
        "age_career_trend": _edge(away.age_curve_score, home.age_curve_score),
        "miscellaneous": _edge(
            away.momentum_score * 0.5 + away.getup_score * 0.25 + away.fight_iq_score * 0.25,
            home.momentum_score * 0.5 + home.getup_score * 0.25 + home.fight_iq_score * 0.25,
        ),
    }


def _category_labels(edges: dict[str, float]) -> dict[str, str]:
    labels: dict[str, str] = {}
    mapping = {
        "style_matchup": "styleMatchup",
        "striking": "striking",
        "wrestling_grappling": "grappling",
        "cardio_pace": "cardio",
        "recent_form": "recentForm",
        "physical_advantages": "physicalAdvantages",
        "strength_of_competition": "strengthOfSchedule",
    }
    for key, out_key in mapping.items():
        e = edges.get(key, 0.0)
        if e > 0.12:
            labels[out_key] = "Fighter A edge"
        elif e < -0.12:
            labels[out_key] = "Fighter B edge"
        else:
            labels[out_key] = "Even"
    return labels


def _prob_away_from_edge(weighted_edge: float) -> float:
    """Map composite away-edge to away win probability."""
    return 1.0 / (1.0 + math.exp(-3.2 * weighted_edge))


def _win_method_probs(
    away: FighterFeatureScores,
    home: FighterFeatureScores,
    prob_away: float,
) -> dict[str, float]:
    prob_home = 1.0 - prob_away
    away_finish = away.finish_threat_score / 100.0
    home_finish = home.finish_threat_score / 100.0
    away_ko_share = away.knockout_power_score / max(away.knockout_power_score + away.submission_threat_score, 1)
    home_ko_share = home.knockout_power_score / max(home.knockout_power_score + home.submission_threat_score, 1)

    a_ko = prob_away * away_finish * away_ko_share * 0.85
    a_sub = prob_away * away_finish * (1 - away_ko_share) * 0.75
    a_dec = max(0.0, prob_away - a_ko - a_sub)

    h_ko = prob_home * home_finish * home_ko_share * 0.85
    h_sub = prob_home * home_finish * (1 - home_ko_share) * 0.75
    h_dec = max(0.0, prob_home - h_ko - h_sub)

    total = a_ko + a_sub + a_dec + h_ko + h_sub + h_dec
    if total <= 0:
        return {
            "fighterA_KO_TKO": 0.25,
            "fighterA_Submission": 0.1,
            "fighterA_Decision": 0.15,
            "fighterB_KO_TKO": 0.25,
            "fighterB_Submission": 0.1,
            "fighterB_Decision": 0.15,
        }
    return {
        "fighterA_KO_TKO": round(a_ko / total, 4),
        "fighterA_Submission": round(a_sub / total, 4),
        "fighterA_Decision": round(a_dec / total, 4),
        "fighterB_KO_TKO": round(h_ko / total, 4),
        "fighterB_Submission": round(h_sub / total, 4),
        "fighterB_Decision": round(h_dec / total, 4),
    }


def _build_reasons(
    away: FighterFeatureScores,
    home: FighterFeatureScores,
    adj: MatchupAdjustments,
    away_ctx: FighterContext,
    home_ctx: FighterContext,
    edges: dict[str, float],
) -> list[str]:
    reasons: list[str] = []

    def add(msg: str) -> None:
        if msg not in reasons:
            reasons.append(msg)

    if adj.adjusted_grappling_advantage > 0.15:
        if away.style_archetype in ("wrestler", "grappler"):
            add(f"{away_ctx.name} has a clear grappling path — opponent shows weaker takedown defense")
    elif adj.adjusted_grappling_advantage < -0.15:
        if home.style_archetype in ("wrestler", "grappler"):
            add(f"{home_ctx.name} can impose wrestling against limited takedown defense")

    if adj.adjusted_striking_advantage > 0.15:
        add(f"{away_ctx.name} profiles better in striking volume, power, or range for this matchup")
    elif adj.adjusted_striking_advantage < -0.15:
        add(f"{home_ctx.name} profiles better in striking volume, power, or range for this matchup")

    if adj.adjusted_cardio_advantage > 0.15:
        add(f"{away_ctx.name} should hold the pace better if the fight extends")
    elif adj.adjusted_cardio_advantage < -0.15:
        add(f"{home_ctx.name} should hold the pace better if the fight extends")

    if edges["recent_form"] > 0.2:
        add(f"{away_ctx.name} enters with stronger recent form (last 5)")
    elif edges["recent_form"] < -0.2:
        add(f"{home_ctx.name} enters with stronger recent form (last 5)")

    if edges["strength_of_competition"] > 0.15:
        add(f"{away_ctx.name} has faced tougher competition on average")
    elif edges["strength_of_competition"] < -0.15:
        add(f"{home_ctx.name} has faced tougher competition on average")

    if away.style_archetype == "counter_striker" and home.style_archetype == "pressure":
        add(f"{away_ctx.name}'s counter-striking style matches up well vs a pressure fighter")
    if home.style_archetype == "counter_striker" and away.style_archetype == "pressure":
        add(f"{home_ctx.name}'s counter-striking style matches up well vs a pressure fighter")

    if not reasons:
        add("Matchup is close — edges are marginal across style and form categories")
    return reasons[:6]


def _build_risks(
    away_ctx: FighterContext,
    home_ctx: FighterContext,
    away: FighterFeatureScores,
    home: FighterFeatureScores,
    pick_side: str,
) -> list[str]:
    risks: list[str] = []

    def add(msg: str) -> None:
        if msg not in risks:
            risks.append(msg)

    for ctx, scores, label in (
        (away_ctx, away, "Fighter A"),
        (home_ctx, home, "Fighter B"),
    ):
        if (ctx.layoff_days or 0) > 365:
            add(f"{ctx.name} ({label}) — long layoff ({ctx.layoff_days} days) adds rust risk")
        if ctx.short_notice:
            add(f"{ctx.name} ({label}) — short-notice prep may hurt cardio and gameplan")
        if ctx.recent_losses >= 3:
            add(f"{ctx.name} ({label}) — multiple recent losses raise durability concerns")
        if scores.durability_score < 45:
            add(f"{ctx.name} ({label}) — chin/durability profile is a concern")
        if ctx.b2b:
            add(f"{ctx.name} ({label}) — quick turnaround after last bout")

    if pick_side == "away" and home.takedown_offense_score >= 68 and away.takedown_defense_score < 52:
        add(f"{away_ctx.name} — weak takedown defense vs a chain-wrestling opponent is a major risk")
    if pick_side == "home" and away.takedown_offense_score >= 68 and home.takedown_defense_score < 52:
        add(f"{home_ctx.name} — weak takedown defense vs a chain-wrestling opponent is a major risk")

    wc = _normalize_weight_class(away_ctx.weight_class or home_ctx.weight_class)
    if wc in ("lightweight", "featherweight", "bantamweight", "flyweight", "women"):
        for ctx, label in ((away_ctx, "A"), (home_ctx, "B")):
            est_age = 24 + min(14, ctx.record.total_bouts / 2.5)
            if est_age > 35 and pick_side == ctx.side:
                add(f"Fighter {label} — older lighter-weight fighter (35+) carries decline risk")

    return risks[:8]


def _style_notes(away: FighterFeatureScores, home: FighterFeatureScores) -> list[str]:
    notes = [
        f"Fighter A archetype: {away.style_archetype.replace('_', ' ')}",
        f"Fighter B archetype: {home.style_archetype.replace('_', ' ')}",
    ]
    if away.style_archetype == "wrestler" and home.takedown_defense_score < 52:
        notes.append("Wrestler vs weak TDD — top-control path is live for Fighter A")
    if home.style_archetype == "wrestler" and away.takedown_defense_score < 52:
        notes.append("Wrestler vs weak TDD — top-control path is live for Fighter B")
    if away.pace_score >= home.pace_score + 12 and home.cardio_score < 58:
        notes.append("High-volume striker vs low-output opponent favors Fighter A's pace")
    return notes


def predict_matchup(
    fight: dict[str, Any],
    slate_day: date,
    *,
    feature_row: dict[str, Any] | None = None,
    history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Run full matchup prediction for a scheduled UFC fight.

    Fighter A = away corner, Fighter B = home corner (ESPN order).
    """
    away_name = str(fight.get("away_team") or fight.get("away_fighter") or "Fighter A")
    home_name = str(fight.get("home_team") or fight.get("home_fighter") or "Fighter B")

    away_ctx = build_fighter_context(
        name=away_name,
        side="away",
        fight=fight,
        slate_day=slate_day,
        feature_row=feature_row,
        history_df=history_df,
    )
    home_ctx = build_fighter_context(
        name=home_name,
        side="home",
        fight=fight,
        slate_day=slate_day,
        feature_row=feature_row,
        history_df=history_df,
    )

    away_scores = score_fighter(away_ctx)
    home_scores = score_fighter(home_ctx)
    adjustments = compute_matchup_adjustments(away_scores, home_scores, away_ctx, home_ctx)
    edges = _category_edges(away_scores, home_scores, adjustments, away_ctx, home_ctx)

    weighted_edge = sum(CATEGORY_WEIGHTS[k] * edges[k] for k in CATEGORY_WEIGHTS)
    prob_away = _prob_away_from_edge(weighted_edge)
    prob_home = 1.0 - prob_away

    pick_away = prob_away >= prob_home
    predicted = away_name if pick_away else home_name
    confidence = round(max(prob_away, prob_home) * 100, 1)

    pick_side = "away" if pick_away else "home"
    key_reasons = _build_reasons(
        away_scores, home_scores, adjustments, away_ctx, home_ctx, edges
    )
    risk_factors = _build_risks(away_ctx, home_ctx, away_scores, home_scores, pick_side)
    style_notes = _style_notes(away_scores, home_scores)

    model_notes = [
        "Matchup engine v1 — style-adjusted heuristic model (not record-only)",
        f"Composite category edge favors {'Fighter A' if weighted_edge > 0 else 'Fighter B' if weighted_edge < 0 else 'neither corner'}",
        *style_notes,
    ]

    return {
        "predictedWinner": predicted,
        "predictedWinnerSide": pick_side,
        "confidence": confidence,
        "probAway": round(prob_away, 4),
        "probHome": round(prob_home, 4),
        "fighterScores": {
            "fighterA": round(away_scores.composite(), 1),
            "fighterB": round(home_scores.composite(), 1),
        },
        "fighterFeatureScores": {
            "fighterA": away_scores.as_dict(),
            "fighterB": home_scores.as_dict(),
        },
        "matchupAdjustments": adjustments.as_dict(),
        "categoryBreakdown": _category_labels(edges),
        "categoryEdges": {k: round(v, 3) for k, v in edges.items()},
        "winMethodProbabilities": _win_method_probs(away_scores, home_scores, prob_away),
        "keyReasons": key_reasons,
        "riskFactors": risk_factors,
        "styleAdvantageNotes": style_notes,
        "modelNotes": model_notes,
    }
