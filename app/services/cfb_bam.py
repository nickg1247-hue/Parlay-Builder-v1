"""Immutable, evaluation-only BAM summaries for CFB OOS predictions."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

EVALUATION_SEASONS = (2023, 2024, 2025)
TRAINING_ONLY_SEASONS = (2022,)
MIN_SAMPLE = 30
LOG_LOSS_EPSILON = 1e-15

@dataclass(frozen=True, slots=True)
class BAMTier:
    key: str; label: str; lower: float; upper: float; include_upper: bool; target: float | None

@dataclass(frozen=True, slots=True)
class BAMOOSRecord:
    game_id: str; season: int; predicted_probability: float; outcome: int; confidence_probability: float; won: int
    source: str = "walk_forward_oos"

BAM_TIERS = (
    BAMTier("bam_1", "BAM 1", .50, .60, False, None),
    BAMTier("bam_2", "BAM 2", .60, .75, False, .60),
    BAMTier("bam_3", "BAM 3", .75, .90, False, .75),
    BAMTier("bam_4", "BAM 4", .90, 1.00, True, .95),
)

def tier_for_probability(probability: float) -> BAMTier:
    value = float(probability)
    for tier in BAM_TIERS:
        if value >= tier.lower and (value <= tier.upper if tier.include_upper else value < tier.upper):
            return tier
    raise ValueError(f"BAM probability must be within [0.50, 1.00], got {value}")

def freeze_oos_records(rows: Iterable[Mapping[str, Any]]) -> tuple[BAMOOSRecord, ...]:
    records = []
    for row in rows:
        season = int(row.get("season") or 0)
        if season not in EVALUATION_SEASONS: continue
        if row.get("source") != "walk_forward_oos":
            raise ValueError("BAM aggregation accepts generated walk-forward OOS rows only")
        probability = float(row["predicted_probability"])
        confidence = float(row.get("confidence_probability", max(probability, 1.0 - probability)))
        tier_for_probability(confidence)
        outcome = int(row.get("outcome", row["won"] if probability >= .5 else 1 - int(row["won"])))
        records.append(BAMOOSRecord(str(row.get("game_id") or ""), season, probability, outcome, confidence, int(row["won"])))
    return tuple(records)

def wilson_interval(wins: int, count: int, z: float = 1.959963984540054):
    if count <= 0: return None
    p = wins / count; denominator = 1 + z*z/count
    centre = (p + z*z/(2*count))/denominator
    margin = z*math.sqrt((p*(1-p)+z*z/(4*count))/count)/denominator
    return max(0., centre-margin), min(1., centre+margin)

def metric_summary(records: Sequence[BAMOOSRecord], target: float | None = None) -> dict[str, Any]:
    count = len(records); wins = sum(r.won for r in records)
    if not count:
        return {"count":0,"wins":0,"losses":0,"accuracy_pct":None,"brier_score":None,"log_loss":None,
                "mean_confidence_pct":None,"target_pct":None if target is None else target*100,
                "goal_gap_points":None,"confidence_calibration_gap_points":None,"wilson_95_pct":None,
                "small_sample":True}
    accuracy=wins/count; mean=sum(r.confidence_probability for r in records)/count
    brier=sum((r.confidence_probability-r.won)**2 for r in records)/count
    probs=[min(max(r.confidence_probability,LOG_LOSS_EPSILON),1-LOG_LOSS_EPSILON) for r in records]
    logloss=-sum(r.won*math.log(p)+(1-r.won)*math.log(1-p) for r,p in zip(records,probs))/count
    interval=wilson_interval(wins,count)
    return {"count":count,"wins":wins,"losses":count-wins,"accuracy_pct":round(accuracy*100,2),
            "brier_score":round(brier,4),"log_loss":round(logloss,4),"mean_confidence_pct":round(mean*100,2),
            "target_pct":None if target is None else round(target*100,2),
            "goal_gap_points":None if target is None else round((accuracy-target)*100,2),
            "confidence_calibration_gap_points":round((accuracy-mean)*100,2),
            "wilson_95_pct":[round(interval[0]*100,2),round(interval[1]*100,2)],
            "small_sample":count<MIN_SAMPLE}

def reliability_summary(records: Sequence[BAMOOSRecord]):
    bins=[]; weighted=0.; total=len(records)
    for i in range(10):
        lo=i*.10; hi=lo+.10
        selected=[r for r in records if min(9, int(round(r.predicted_probability * 10, 10))) == i]
        empirical=sum(r.outcome for r in selected)/len(selected) if selected else None
        predicted=sum(r.predicted_probability for r in selected)/len(selected) if selected else None
        gap=empirical-predicted if empirical is not None else None
        if gap is not None: weighted += len(selected)*abs(gap)
        bins.append({"bin":i+1,"lower_pct":round(lo*100,2),"upper_pct":round(hi*100,2),"upper_inclusive":i==9,
                     "count":len(selected),"predicted_probability_pct":None if predicted is None else round(predicted*100,2),
                     "empirical_win_rate_pct":None if empirical is None else round(empirical*100,2),
                     "gap_points":None if gap is None else round(gap*100,2),"small_sample":len(selected)<MIN_SAMPLE})
    return {"bins":bins,"ece_pct":round(weighted*100/total,2) if total else None}

def _summary(records):
    tiers=[]
    for tier in BAM_TIERS:
        selected=[r for r in records if tier_for_probability(r.confidence_probability)==tier]
        tiers.append({"key":tier.key,"label":tier.label,"lower_pct":tier.lower*100,"upper_pct":tier.upper*100,
                      "upper_inclusive":tier.include_upper,**metric_summary(selected,tier.target)})
    return {"metrics":metric_summary(records),"tiers":tiers,"reliability":reliability_summary(records)}

def build_bam_report(rows: Iterable[Mapping[str, Any]]):
    records=freeze_oos_records(rows)
    return {"status":"ok","evaluation_only":True,"immutable_tiers":True,"method":"expanding_window_walk_forward_oos",
            "training_only_seasons":list(TRAINING_ONLY_SEASONS),"evaluation_seasons":list(EVALUATION_SEASONS),
            "small_sample_threshold":MIN_SAMPLE,
            "disclaimer":"Historical out-of-sample results describe past model performance and do not guarantee future outcomes.",
            "overall":_summary(records),
            "by_season":[{"season":s,**_summary(tuple(r for r in records if r.season==s))} for s in EVALUATION_SEASONS]}


