"""Per-fight UFC insights: moneyline, fighter stats, bets, card parlays."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.ufc_baseline import load_model_artifact
from app.services.game_insights import _confidence_tier
from app.services.schedule_ufc import get_ufc_fight
from app.services.ufc_daily_board import UFC_DISCLAIMER, build_ufc_daily_board
from app.services.ufc_fighter_media import enrich_fight_media
from app.services.ufc_slate_predictions import predict_slate
from app.features.ufc_pregame import (
    estimate_rounds_expected,
    fighter_layoff_days,
    format_layoff_label,
)
from app.models.ufc_matchup_engine import predict_matchup

FEATURE_NOTES: dict[str, str] = {
    "elo_diff": "Home-corner Elo advantage (pregame ratings)",
    "home_career_win_pct": "Home fighter career win % before bout",
    "away_career_win_pct": "Away fighter career win % before bout",
    "home_rest_days": "Days since home fighter's last bout",
    "away_rest_days": "Days since away fighter's last bout",
    "rest_diff": "Home rest minus away rest",
    "home_last5_win_pct": "Home fighter last-5 win rate",
    "away_last5_win_pct": "Away fighter last-5 win rate",
    "last5_win_pct_diff": "Home last-5 minus away last-5",
    "home_b2b": "Home fighter on short rest flag",
    "away_b2b": "Away fighter on short rest flag",
}


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return True
    return bool(pd.isna(val))


def _json_safe(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_json_safe(v) for v in val]
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    if isinstance(val, (str, int, bool)) or val is None:
        return val
    if pd.isna(val):
        return None
    if hasattr(val, "item"):
        try:
            return _json_safe(val.item())
        except (TypeError, ValueError):
            return str(val)
    return val


def _slate_row(board: dict[str, Any], fight_id: str) -> dict[str, Any] | None:
    for row in board.get("slate", []):
        if str(row.get("fight_id") or row.get("game_id")) == str(fight_id):
            return row
    return None


def _pred_row(fight_id: str, slate_day: date) -> dict[str, Any] | None:
    try:
        preds = predict_slate(slate_day)
    except FileNotFoundError:
        return None
    return preds.get(str(fight_id))


def _merge_row(
    board_row: dict[str, Any] | None,
    pred: dict[str, Any] | None,
    fight: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    if board_row:
        row.update(board_row)
    if pred:
        for key, val in pred.items():
            if key not in row or row[key] is None:
                row[key] = val
    fid = str(fight.get("fight_id") or fight.get("game_id") or "")
    row.setdefault("fight_id", fid)
    row.setdefault("game_id", fid)
    row.setdefault("home_team", fight.get("home_team"))
    row.setdefault("away_team", fight.get("away_team"))
    return row


def _build_moneyline(row: dict[str, Any]) -> dict[str, Any]:
    prob_home = row.get("model_prob_home")
    prob_away = row.get("model_prob_away")
    if prob_home is not None and prob_away is None:
        prob_away = round(1.0 - float(prob_home), 4)

    edge_home = row.get("ev_home") if row.get("ev_home") is not None else row.get("edge_home")
    edge_away = row.get("ev_away") if row.get("ev_away") is not None else row.get("edge_away")

    best_pick = row.get("best_pick")
    if best_pick is None and (row.get("plus_ev_single") or row.get("plus_ev_ml")):
        min_edge = DEFAULT_MIN_EDGE
        if edge_home is not None and float(edge_home) >= min_edge:
            best_pick = {
                "side": "home",
                "fighter": row.get("home_team"),
                "edge": edge_home,
                "american_odds": row.get("home_ml"),
            }
        elif edge_away is not None and float(edge_away) >= min_edge:
            best_pick = {
                "side": "away",
                "fighter": row.get("away_team"),
                "edge": edge_away,
                "american_odds": row.get("away_ml"),
            }

    return {
        "model_prob_home": prob_home,
        "model_prob_away": prob_away,
        "market_prob_home": row.get("market_prob_home"),
        "market_prob_away": row.get("market_prob_away"),
        "home_ml": row.get("home_ml"),
        "away_ml": row.get("away_ml"),
        "ev_home": edge_home,
        "ev_away": edge_away,
        "plus_ev_ml": bool(row.get("plus_ev_ml") or row.get("plus_ev_single")),
        "ml_confidence": row.get("ml_confidence"),
        "model_pick": row.get("model_pick"),
        "model_pick_side": row.get("model_pick_side"),
        "best_pick": best_pick,
    }


def _build_bets(moneyline: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    singles: list[dict[str, Any]] = []
    if moneyline.get("best_pick"):
        bp = moneyline["best_pick"]
        singles.append(
            {
                "market": "moneyline",
                "fighter": bp.get("fighter"),
                "side": bp.get("side"),
                "american_odds": bp.get("american_odds"),
                "edge": bp.get("edge"),
                "plus_ev": True,
            }
        )
    elif moneyline.get("model_pick"):
        side = moneyline.get("model_pick_side") or (
            "home" if (moneyline.get("model_prob_home") or 0) >= 0.5 else "away"
        )
        fighter = row.get("home_team") if side == "home" else row.get("away_team")
        odds = row.get("home_ml") if side == "home" else row.get("away_ml")
        edge = moneyline.get("ev_home") if side == "home" else moneyline.get("ev_away")
        singles.append(
            {
                "market": "moneyline",
                "fighter": fighter,
                "side": side,
                "american_odds": odds,
                "edge": edge,
                "plus_ev": bool(moneyline.get("plus_ev_ml")),
                "model_lean": True,
            }
        )

    props: list[dict[str, Any]] = []
    totals_line = row.get("totals_line")
    over_odds = row.get("over_odds")
    under_odds = row.get("under_odds")
    if not _is_missing(totals_line) and (
        not _is_missing(over_odds) or not _is_missing(under_odds)
    ):
        props.append(
            {
                "market": "rounds_total",
                "line": float(totals_line),
                "over_odds": int(over_odds) if not _is_missing(over_odds) else None,
                "under_odds": int(under_odds) if not _is_missing(under_odds) else None,
                "label": f"O/U {float(totals_line):g} rounds",
            }
        )

    props_note = None
    if not props:
        props_note = (
            "Method-of-victory props are not wired yet. Round totals appear when "
            "live Odds API lines include totals for this fight."
        )

    return {
        "singles": singles,
        "props": props,
        "props_note": props_note,
    }


def _build_highlights(row: dict[str, Any], moneyline: dict[str, Any]) -> dict[str, Any]:
    ml_side = moneyline.get("model_pick_side")
    if ml_side is None and moneyline.get("model_prob_home") is not None:
        ml_side = "home" if float(moneyline["model_prob_home"]) >= 0.5 else "away"
    return {
        "moneyline_side": ml_side,
        "moneyline_tier": _confidence_tier(moneyline.get("ml_confidence")) if ml_side else None,
    }


def _build_matchup_board(row: dict[str, Any], highlights: dict[str, Any]) -> dict[str, Any]:
    return {
        "home": {"moneyline": row.get("home_ml")},
        "away": {"moneyline": row.get("away_ml")},
        "highlights": highlights,
    }


def _round_feature(val: Any) -> float | int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
        if f == int(f) and abs(f) < 1e6:
            return int(f) if f == int(f) else round(f, 4)
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _build_feature_snapshot(
    fight_id: str,
    slate_day: date,
    fight: dict[str, Any],
    *,
    feature_cols: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        from app.features.ufc_pregame import build_features_for_slate

        artifact = load_model_artifact()
        cols = feature_cols or list(artifact.get("feature_columns") or [])
        if not cols:
            return []

        slate_df = pd.DataFrame(
            [
                {
                    "fight_id": str(fight_id),
                    "game_id": str(fight_id),
                    "date": slate_day.isoformat(),
                    "season": slate_day.year,
                    "home_team": fight.get("home_team") or "",
                    "away_team": fight.get("away_team") or "",
                }
            ]
        )
        feats = build_features_for_slate(slate_df)
        if feats.empty:
            return []

        row = feats.iloc[0]
        snapshot: list[dict[str, Any]] = []
        for name in cols:
            entry: dict[str, Any] = {
                "name": name,
                "value": _round_feature(row.get(name)),
            }
            note = FEATURE_NOTES.get(name)
            if note:
                entry["note"] = note
            snapshot.append(entry)
        return snapshot
    except (FileNotFoundError, OSError, ValueError):
        return []


def _last5_record_label(win_pct: float | None) -> str:
    if win_pct is None:
        return "—"
    wins = max(0, min(5, round(float(win_pct) * 5)))
    return f"{wins}-{5 - wins}"


def _build_fighter_stats(
    fight: dict[str, Any],
    slate_day: date,
    fight_id: str,
) -> dict[str, Any]:
    feat_map: dict[str, Any] = {}
    try:
        from app.features.ufc_pregame import build_features_for_slate

        slate_df = pd.DataFrame(
            [
                {
                    "fight_id": str(fight_id),
                    "game_id": str(fight_id),
                    "date": slate_day.isoformat(),
                    "season": slate_day.year,
                    "home_team": fight.get("home_team") or "",
                    "away_team": fight.get("away_team") or "",
                }
            ]
        )
        feats = build_features_for_slate(slate_df)
        if not feats.empty:
            feat_map = feats.iloc[0].to_dict()
    except (FileNotFoundError, OSError, ValueError):
        pass

    def corner(side: str) -> dict[str, Any]:
        prefix = f"{side}_"
        fighter_name = fight.get(f"{side}_team") or fight.get(f"{side}_fighter") or ""
        layoff_days = fighter_layoff_days(fighter_name, slate_day)
        media = {
            "headshot_url": fight.get(f"{side}_headshot_url"),
            "flag_url": fight.get(f"{side}_flag_url"),
            "country": fight.get(f"{side}_country"),
            "athlete_id": fight.get(f"{side}_athlete_id"),
        }
        last5_pct = _round_feature(feat_map.get(f"{prefix}last5_win_pct"))
        return {
            "name": fighter_name,
            "record": fight.get(f"{side}_record"),
            "last5_record": _last5_record_label(last5_pct),
            "last5_win_pct": last5_pct,
            "layoff_days": layoff_days,
            "layoff_label": format_layoff_label(layoff_days),
            **media,
        }

    return {
        "home": corner("home"),
        "away": corner("away"),
        "weight_class": fight.get("weight_class"),
        "card_segment": fight.get("card_segment"),
        "event_name": fight.get("event_name"),
    }


def _card_parlays_for_fight(
    board: dict[str, Any],
    fight_id: str,
) -> list[dict[str, Any]]:
    parlays = board.get("top_parlays") or []
    fid = str(fight_id)
    out: list[dict[str, Any]] = []
    for parlay in parlays:
        legs = parlay.get("legs") or []
        if any(str(leg.get("game_id")) == fid for leg in legs):
            out.append(parlay)
    return out


def _card_fights(
    board: dict[str, Any],
    fight_id: str,
    slate_day: date,
) -> list[dict[str, Any]]:
    """Other fights on the same card with summary + detail links."""
    fid = str(fight_id)
    out: list[dict[str, Any]] = []
    for row in board.get("slate") or []:
        row_id = str(row.get("fight_id") or row.get("game_id") or "")
        if not row_id or row_id == fid:
            continue
        out.append(
            {
                "fight_id": row_id,
                "matchup": row.get("matchup"),
                "weight_class": row.get("weight_class"),
                "model_pick": row.get("model_pick"),
                "plus_ev_single": bool(row.get("plus_ev_single")),
                "href": f"/ufc/game/{row_id}?date={slate_day.isoformat()}",
            }
        )
    return out


def _load_slate_features(
    fight: dict[str, Any],
    slate_day: date,
    fight_id: str,
) -> dict[str, Any]:
    try:
        from app.features.ufc_pregame import build_features_for_slate

        slate_df = pd.DataFrame(
            [
                {
                    "fight_id": str(fight_id),
                    "game_id": str(fight_id),
                    "date": slate_day.isoformat(),
                    "season": slate_day.year,
                    "home_team": fight.get("home_team") or "",
                    "away_team": fight.get("away_team") or "",
                }
            ]
        )
        feats = build_features_for_slate(slate_df)
        if not feats.empty:
            return feats.iloc[0].to_dict()
    except (FileNotFoundError, OSError, ValueError):
        pass
    return {}


def _apply_matchup_to_row(
    row: dict[str, Any],
    matchup: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use matchup engine as the single source of model win probabilities."""
    if not matchup:
        return row
    out = dict(row)
    prob_home = matchup.get("probHome")
    prob_away = matchup.get("probAway")
    if prob_home is not None:
        out["model_prob_home"] = prob_home
    if prob_away is not None:
        out["model_prob_away"] = prob_away
    if matchup.get("predictedWinner"):
        out["model_pick"] = matchup["predictedWinner"]
    if matchup.get("predictedWinnerSide"):
        out["model_pick_side"] = matchup["predictedWinnerSide"]
    return out


def build_ufc_fight_insights(
    fight_id: str,
    game_date: date | None = None,
    *,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Merge schedule, model moneyline, fighter stats, bets, and card parlays."""
    del refresh
    detail = get_ufc_fight(fight_id, game_date)
    if detail is None:
        return None

    resolved_raw = detail.get("resolved_date") or detail.get("date")
    slate_day = date.fromisoformat(str(resolved_raw)[:10])
    fight = enrich_fight_media(detail["game"], slate_day)

    board = build_ufc_daily_board(
        slate_day,
        min_edge=DEFAULT_MIN_EDGE,
        use_cache=use_cache,
        max_parlays=5,
    )
    board_row = _slate_row(board, fight_id)
    pred = _pred_row(fight_id, slate_day) if not board.get("error") else None
    row = _merge_row(board_row, pred, fight)

    if not row.get("model_prob_home") and not row.get("model_pick"):
        row = _merge_row(None, pred, fight)

    feat_row = _load_slate_features(fight, slate_day, fight_id)
    try:
        matchup_prediction = predict_matchup(fight, slate_day, feature_row=feat_row or None)
    except (FileNotFoundError, OSError, ValueError):
        matchup_prediction = None

    row = _apply_matchup_to_row(row, matchup_prediction)

    moneyline = _build_moneyline(row)
    highlights = _build_highlights(row, moneyline)
    matchup_board = _build_matchup_board(row, highlights)
    bets = _build_bets(moneyline, row)

    card_segment = (fight.get("card_segment") or "").lower()
    rounds_expected = estimate_rounds_expected(
        totals_line=row.get("totals_line"),
        model_prob_home=moneyline.get("model_prob_home"),
        model_prob_away=moneyline.get("model_prob_away"),
        is_title_fight="title" in (fight.get("event_name") or "").lower()
        or card_segment == "main",
    )

    fighter_stats = _build_fighter_stats(fight, slate_day, fight_id)
    card_fights = _card_fights(board, fight_id, slate_day)

    pick_side = moneyline.get("model_pick_side")
    if pick_side is None and moneyline.get("model_prob_home") is not None:
        pick_side = "home" if float(moneyline["model_prob_home"]) >= 0.5 else "away"

    prob_home = moneyline.get("model_prob_home")
    prob_away = moneyline.get("model_prob_away")
    if prob_home is not None and prob_away is None:
        prob_away = round(1.0 - float(prob_home), 4)
    if prob_away is not None and prob_home is None:
        prob_home = round(1.0 - float(prob_away), 4)

    pick_prob = prob_home if pick_side == "home" else prob_away

    return _json_safe(
        {
            "game": fight,
            "date": slate_day.isoformat(),
            "sport": "ufc",
            "disclaimer": "For entertainment only. Bet responsibly.",
            "moneyline": moneyline,
            "bets": bets,
            "fighter_stats": fighter_stats,
            "matchup_prediction": matchup_prediction,
            "matchup_board": matchup_board,
            "card_fights": card_fights,
            "fight_preview": {
                "pick": moneyline.get("model_pick"),
                "pick_side": pick_side,
                "pick_win_pct": pick_prob,
                "away_win_pct": prob_away,
                "home_win_pct": prob_home,
                "rounds_expected": rounds_expected,
            },
        }
    )
