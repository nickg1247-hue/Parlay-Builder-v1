"""Per-game NFL insights: moneyline, spread, totals, matchup board, features."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nfl_baseline import load_model_artifact
from app.odds.nfl_team_aliases import normalize_nfl_team
from app.services.game_insights import _confidence_tier
from app.services.nfl_daily_board import NFL_DISCLAIMER, build_nfl_daily_board
from app.services.nfl_slate_predictions import nfl_season_year, predict_slate
from app.services.schedule_nfl import get_nfl_game

FEATURE_NOTES = {
    "elo_diff": "Home Elo advantage (pregame ratings)",
    "home_season_win_pct": "Home season win % before kickoff",
    "away_season_win_pct": "Away season win % before kickoff",
    "home_rest_days": "Days since home team's last game",
    "away_rest_days": "Days since away team's last game",
    "rest_diff": "Home rest minus away rest",
    "home_field": "1 if home-field advantage applies",
    "divisional": "1 if same NFL division",
    "is_preseason": "1 if preseason game",
}


def _slate_row(board: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    for row in board.get("slate", []):
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def _merge_row(board_row, pred, game) -> dict[str, Any]:
    row: dict[str, Any] = {}
    if board_row:
        row.update(board_row)
    if pred:
        for key, val in pred.items():
            if key not in row or row[key] is None:
                row[key] = val
    row.setdefault("game_id", str(game.get("game_id") or ""))
    row.setdefault("home_team", game.get("home_team"))
    row.setdefault("away_team", game.get("away_team"))
    return row


def _build_moneyline(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_prob_home": row.get("model_prob_home"),
        "model_prob_away": row.get("model_prob_away"),
        "market_prob_home": row.get("market_prob_home"),
        "market_prob_away": row.get("market_prob_away"),
        "home_ml": row.get("home_ml"),
        "away_ml": row.get("away_ml"),
        "ev_home": row.get("ev_home") if row.get("ev_home") is not None else row.get("edge_home"),
        "ev_away": row.get("ev_away") if row.get("ev_away") is not None else row.get("edge_away"),
        "plus_ev_ml": bool(row.get("plus_ev_ml") or row.get("plus_ev_single")),
        "ml_confidence": row.get("ml_confidence"),
        "model_pick": row.get("model_pick"),
        "model_pick_side": row.get("model_pick_side"),
        "best_pick": row.get("best_pick"),
    }


def _build_spread(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "home_spread_point": row.get("home_spread_point"),
        "spread_line_source": row.get("spread_line_source"),
        "model_margin": row.get("model_margin"),
        "model_prob_home_cover": row.get("model_prob_home_cover"),
        "spread_pick": row.get("spread_pick"),
        "spread_confidence": row.get("spread_confidence"),
    }


def _build_totals(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ou_line": row.get("ou_line"),
        "ou_line_source": row.get("ou_line_source"),
        "expected_total_pts": row.get("expected_total_pts"),
        "model_prob_over": row.get("model_prob_over"),
        "totals_pick": row.get("totals_pick"),
        "totals_confidence": row.get("totals_confidence"),
    }


def _build_highlights(row: dict[str, Any]) -> dict[str, Any]:
    ml_side = row.get("model_pick_side")
    if ml_side is None and row.get("model_prob_home") is not None:
        ml_side = "home" if float(row["model_prob_home"]) >= 0.5 else "away"
    pick = (row.get("spread_pick") or "").strip()
    home = str(row.get("home_team") or "")
    away = str(row.get("away_team") or "")
    spread_side = None
    if home and pick.startswith(home):
        spread_side = "home"
    elif away and pick.startswith(away):
        spread_side = "away"
    totals_pick = (row.get("totals_pick") or "").strip().lower()
    total_side = "over" if totals_pick.startswith("over") else "under" if totals_pick.startswith("under") else None
    return {
        "moneyline_side": ml_side,
        "moneyline_tier": _confidence_tier(row.get("ml_confidence")) if ml_side else None,
        "spread_side": spread_side,
        "spread_tier": _confidence_tier(row.get("spread_confidence")) if spread_side else None,
        "total_side": total_side,
        "total_tier": _confidence_tier(row.get("totals_confidence")) if total_side else None,
    }


def _build_matchup_board(row: dict[str, Any], highlights: dict[str, Any]) -> dict[str, Any]:
    home_sp = row.get("home_spread_point")
    away_sp = None
    if home_sp is not None:
        try:
            away_sp = -float(home_sp) if float(home_sp) != 0 else None
        except (TypeError, ValueError):
            away_sp = None
    ou = row.get("ou_line")
    return {
        "home": {"moneyline": row.get("home_ml"), "spread": home_sp, "total_over": ou},
        "away": {"moneyline": row.get("away_ml"), "spread": away_sp, "total_under": ou},
        "highlights": highlights,
    }


def _round_feature(val: Any):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
        if f == int(f) and abs(f) < 1e6:
            return int(f)
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _feature_snapshot(game_id: str, slate_day: date, game: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from app.features.nfl_pregame import build_features_for_slate

        artifact = load_model_artifact()
        cols = list(artifact.get("feature_columns") or [])
        if not cols:
            return []
        slate_df = pd.DataFrame(
            [
                {
                    "game_id": str(game_id),
                    "date": slate_day.isoformat(),
                    "season": nfl_season_year(slate_day),
                    "home_team": game.get("home_team") or "",
                    "away_team": game.get("away_team") or "",
                    "home_team_abbr": normalize_nfl_team(game.get("home_team_abbr") or game.get("home_team")),
                    "away_team_abbr": normalize_nfl_team(game.get("away_team_abbr") or game.get("away_team")),
                    "game_type": game.get("game_type") or "regular",
                }
            ]
        )
        feats = build_features_for_slate(slate_df)
        if feats.empty:
            return []
        row = feats.iloc[0]
        return [
            {"name": name, "value": _round_feature(row.get(name)), **({"note": FEATURE_NOTES[name]} if name in FEATURE_NOTES else {})}
            for name in cols
        ]
    except (FileNotFoundError, OSError, ValueError):
        return []


def build_nfl_game_insights(
    game_id: str,
    game_date: date | None = None,
    *,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    del refresh
    detail = get_nfl_game(game_id, game_date)
    if detail is None:
        return None
    resolved_raw = detail.get("resolved_date") or detail.get("date")
    slate_day = date.fromisoformat(str(resolved_raw)[:10])
    game = detail["game"]
    board = build_nfl_daily_board(slate_day, min_edge=DEFAULT_MIN_EDGE, use_cache=use_cache)
    board_row = _slate_row(board, game_id)
    try:
        pred = predict_slate(slate_day).get(str(game_id))
    except FileNotFoundError:
        pred = None
    row = _merge_row(board_row, pred, game)
    moneyline = _build_moneyline(row)
    spread = _build_spread(row)
    totals = _build_totals(row)
    highlights = _build_highlights(row)
    warnings = list(board.get("warnings", []))
    if row.get("home_ml") is None:
        warnings.append("No sportsbook moneyline matched for this game.")
    if spread.get("spread_line_source") == "proxy":
        warnings.append("Spread line is a proxy (-3), not a sportsbook close.")
    return {
        "game": game,
        "date": slate_day.isoformat(),
        "sport": "nfl",
        "disclaimer": f"{NFL_DISCLAIMER} betting_ready=false.",
        "betting_ready": False,
        "warnings": warnings,
        "odds_source": board.get("odds_source", "none"),
        "parlays": [
            p
            for p in (board.get("parlays") or [])
            if any(str(leg.get("game_id")) == str(game_id) for leg in p.get("legs") or [])
        ],
        "active_model": board.get("active_moneyline_model") or {},
        "board_row": row,
        "moneyline": moneyline,
        "spread": spread,
        "totals": totals,
        "matchup_board": _build_matchup_board(row, highlights),
        "feature_snapshot": _feature_snapshot(game_id, slate_day, game),
    }
