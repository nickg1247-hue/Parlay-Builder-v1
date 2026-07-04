"""Moneyline predictions for a UFC card date."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.ufc_baseline import predict_home_win_proba
from app.models.ufc_matchup_engine import predict_matchup
from app.features.ufc_pregame import build_features_for_slate
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.odds.ufc_fighter_aliases import normalize_fighter_name
from app.services.daily_board import confidence_label
from app.services.schedule_ufc import get_ufc_schedule
from app.services.ufc_odds_attach import attach_ufc_odds


def _model_edge_proxy(prob: float) -> float:
    return abs(float(prob) - 0.5) * 2.0


def _clean_json_value(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _clean_json_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_clean_json_value(v) for v in val]
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if pd.isna(val):
        return None
    if hasattr(val, "item"):
        try:
            return _clean_json_value(val.item())
        except (TypeError, ValueError):
            return val
    return val


def _optional_float(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return None
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _optional_int(val: Any) -> int | None:
    f = _optional_float(val)
    if f is None:
        return None
    return int(f)


def _ml_market_fields(
    prob_home: float,
    home_ml: Any,
    away_ml: Any,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "home_ml": None,
        "away_ml": None,
        "market_prob_home": None,
        "market_prob_away": None,
        "model_edge_ml": _model_edge_proxy(prob_home),
        "ev_home": None,
        "ev_away": None,
        "plus_ev_ml": False,
    }
    if home_ml is None or away_ml is None:
        return out
    if pd.isna(home_ml) or pd.isna(away_ml):
        return out
    if not (is_valid_american_odds(home_ml) and is_valid_american_odds(away_ml)):
        return out
    mh, ma = market_probs_from_american(int(home_ml), int(away_ml))
    prob_away = 1.0 - prob_home
    edge_home = prob_home - mh
    edge_away = prob_away - ma
    out.update(
        {
            "home_ml": int(home_ml),
            "away_ml": int(away_ml),
            "market_prob_home": round(mh, 4),
            "market_prob_away": round(ma, 4),
            "model_edge_ml": round(max(edge_home, edge_away), 4),
            "ev_home": round(edge_home, 4),
            "ev_away": round(edge_away, 4),
            "plus_ev_ml": edge_home >= min_edge or edge_away >= min_edge,
        }
    )
    return out


def predict_slate(game_date: date | None = None) -> dict[str, dict[str, Any]]:
    preds, _source = predict_slate_with_meta(game_date)
    return preds


def predict_slate_with_meta(
    game_date: date | None = None,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, dict[str, Any]], str]:
    schedule = get_ufc_schedule(game_date, auto_resolve=game_date is None)
    fights = schedule.get("games") or []
    if not fights:
        return {}, "none"

    slate_date = schedule.get("resolved_date") or schedule.get("date")
    slate_day = date.fromisoformat(str(slate_date)[:10])
    rows = []
    for g in fights:
        rows.append(
            {
                "fight_id": str(g.get("fight_id") or g.get("game_id")),
                "date": slate_date,
                "season": slate_day.year,
                "home_team": normalize_fighter_name(g.get("home_team") or ""),
                "away_team": normalize_fighter_name(g.get("away_team") or ""),
                "weight_class": g.get("weight_class") or "",
                "event_name": g.get("event_name") or "",
            }
        )
    df = pd.DataFrame(rows)
    df, odds_source = attach_ufc_odds(df, slate_day, force_refresh=force_refresh)

    fight_by_id = {
        str(g.get("fight_id") or g.get("game_id")): g for g in fights
    }

    try:
        feat_df = build_features_for_slate(df)
    except (FileNotFoundError, OSError, ValueError):
        feat_df = pd.DataFrame()

    baseline_probs: list[float] | None = None
    try:
        baseline_probs = [float(p) for p in predict_home_win_proba(df)]
    except FileNotFoundError:
        baseline_probs = None

    out: dict[str, dict[str, Any]] = {}
    for i, row in df.iterrows():
        fid = str(row["fight_id"])
        fight_dict = dict(fight_by_id.get(fid) or {})
        fight_dict.setdefault("home_team", row["home_team"])
        fight_dict.setdefault("away_team", row["away_team"])
        fight_dict.setdefault("weight_class", row.get("weight_class"))
        feat_row = feat_df.iloc[i].to_dict() if not feat_df.empty and i < len(feat_df) else None

        prob: float | None = None
        model_pick: str | None = None
        pick_side: str | None = None
        try:
            matchup = predict_matchup(fight_dict, slate_day, feature_row=feat_row)
            prob = float(matchup["probHome"])
            model_pick = str(matchup["predictedWinner"])
            pick_side = str(matchup["predictedWinnerSide"])
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
            pass

        if prob is None and baseline_probs is not None:
            prob = float(baseline_probs[i])
            pick_side = "home" if prob >= 0.5 else "away"
            model_pick = row["home_team"] if pick_side == "home" else row["away_team"]

        if prob is None:
            continue

        ml_fields = _ml_market_fields(prob, row.get("home_ml"), row.get("away_ml"))
        out[fid] = _clean_json_value(
            {
                "fight_id": fid,
                "game_id": fid,
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "weight_class": row.get("weight_class"),
                "event_name": row.get("event_name"),
                "model_prob_home": round(prob, 4),
                "model_prob_away": round(1.0 - prob, 4),
                "model_pick": model_pick,
                "model_pick_side": pick_side,
                "ml_confidence": confidence_label(ml_fields["model_edge_ml"]),
                "totals_line": _optional_float(row.get("totals_line")),
                "over_odds": _optional_int(row.get("over_odds")),
                "under_odds": _optional_int(row.get("under_odds")),
                **ml_fields,
            }
        )
    return out, odds_source


def predict_slate_list(game_date: date | None = None) -> list[dict[str, Any]]:
    return list(predict_slate(game_date).values())
