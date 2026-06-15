"""Per-game CFB insights: moneyline, spread, totals, matchup board, feature snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.cfb_baseline import load_model_artifact
from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_daily_board import CFB_DISCLAIMER, build_cfb_daily_board
from app.services.cfb_slate_predictions import cfb_season_end_year, predict_slate
from app.services.game_insights import _confidence_tier
from app.services.schedule_cfb import get_cfb_game

FEATURE_NOTES: dict[str, str] = {
    "elo_diff": "Home Elo advantage (pregame ratings)",
    "home_season_win_pct": "Home season win % before kickoff",
    "away_season_win_pct": "Away season win % before kickoff",
    "home_rest_days": "Days since home team's last game",
    "away_rest_days": "Days since away team's last game",
    "rest_diff": "Home rest minus away rest",
    "neutral_site": "1 if neutral site, else 0",
    "home_field_active": "1 if home-field advantage applies",
    "home_last5_win_pct": "Home last-5 win rate",
    "away_last5_win_pct": "Away last-5 win rate",
    "last5_win_pct_diff": "Home last-5 minus away last-5",
    "home_home_win_pct": "Home team's home-only win rate",
    "conf_win_pct_diff": "Conference win % differential",
    "home_b2b": "Home on short rest flag",
    "away_b2b": "Away on short rest flag",
    "sp_plus_diff": "SP+ rating diff (home − away)",
    "sp_offense_diff": "SP+ offense diff (home − away)",
    "sp_defense_diff": "SP+ defense diff (home − away)",
}


def _slate_row(board: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    for row in board.get("slate", []):
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def _pred_row(game_id: str, slate_day: date) -> dict[str, Any] | None:
    try:
        preds = predict_slate(slate_day)
    except FileNotFoundError:
        return None
    return preds.get(str(game_id))


def _merge_row(
    board_row: dict[str, Any] | None,
    pred: dict[str, Any] | None,
    game: dict[str, Any],
) -> dict[str, Any]:
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
                "team": row.get("home_team"),
                "edge": edge_home,
                "american_odds": row.get("home_ml"),
            }
        elif edge_away is not None and float(edge_away) >= min_edge:
            best_pick = {
                "side": "away",
                "team": row.get("away_team"),
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
        "best_pick": best_pick,
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


def _spread_side(row: dict[str, Any]) -> str | None:
    pick = (row.get("spread_pick") or "").strip()
    if not pick:
        return None
    home = str(row.get("home_team") or "")
    away = str(row.get("away_team") or "")
    if home and pick.startswith(home):
        return "home"
    if away and pick.startswith(away):
        return "away"
    return row.get("model_pick_side")


def _total_side(row: dict[str, Any]) -> str | None:
    pick = (row.get("totals_pick") or "").strip().lower()
    if pick.startswith("over"):
        return "over"
    if pick.startswith("under"):
        return "under"
    return None


def _build_highlights(row: dict[str, Any]) -> dict[str, Any]:
    ml_side = row.get("model_pick_side")
    if ml_side is None and row.get("model_prob_home") is not None:
        ml_side = "home" if float(row["model_prob_home"]) >= 0.5 else "away"

    total_side = _total_side(row)
    spread_side = _spread_side(row)

    return {
        "moneyline_side": ml_side,
        "moneyline_tier": _confidence_tier(row.get("ml_confidence")) if ml_side else None,
        "spread_side": spread_side,
        "spread_tier": (
            _confidence_tier(row.get("spread_confidence")) if spread_side else None
        ),
        "total_side": total_side,
        "total_tier": (
            _confidence_tier(row.get("totals_confidence")) if total_side else None
        ),
    }


def _build_matchup_board(row: dict[str, Any], highlights: dict[str, Any]) -> dict[str, Any]:
    home_sp = row.get("home_spread_point")
    away_sp: float | None = None
    if home_sp is not None:
        try:
            away_sp = -float(home_sp) if float(home_sp) != 0 else None
        except (TypeError, ValueError):
            away_sp = None

    ou = row.get("ou_line")
    return {
        "home": {
            "moneyline": row.get("home_ml"),
            "spread": home_sp,
            "total_over": ou,
        },
        "away": {
            "moneyline": row.get("away_ml"),
            "spread": away_sp,
            "total_under": ou,
        },
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
    game_id: str,
    slate_day: date,
    game: dict[str, Any],
    *,
    feature_cols: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        from app.features.cfb_pregame import build_features_for_slate

        artifact = load_model_artifact()
        cols = feature_cols or list(artifact.get("feature_columns") or [])
        if not cols:
            return []

        slate_df = pd.DataFrame(
            [
                {
                    "game_id": str(game_id),
                    "date": slate_day.isoformat(),
                    "season": cfb_season_end_year(slate_day),
                    "home_team": normalize_team_name(game.get("home_team") or ""),
                    "away_team": normalize_team_name(game.get("away_team") or ""),
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


def _market_lines_warning(game_date: date, use_cache: bool) -> str:
    if use_cache:
        return (
            f"Demo mode — cached CFBD lines for {game_date.isoformat()}. "
            "Market eval shows model does not beat closing books on holdout."
        )
    return (
        "No sportsbook lines matched for this game. CFBD lines cache or live Odds API "
        "may be empty. Model picks still shown."
    )


def build_cfb_game_insights(
    game_id: str,
    game_date: date | None = None,
    *,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Merge schedule, model markets, matchup board, and active-model feature snapshot."""
    del refresh
    detail = get_cfb_game(game_id, game_date)
    if detail is None:
        return None

    resolved_raw = detail.get("resolved_date") or detail.get("date")
    slate_day = date.fromisoformat(str(resolved_raw)[:10])
    game = detail["game"]

    board = build_cfb_daily_board(
        slate_day,
        min_edge=DEFAULT_MIN_EDGE,
        use_cache=use_cache,
    )
    board_row = _slate_row(board, game_id)
    pred = _pred_row(game_id, slate_day) if not board.get("error") else None
    row = _merge_row(board_row, pred, game)

    if not row.get("model_prob_home") and not row.get("model_pick"):
        row = _merge_row(None, pred, game)

    moneyline = _build_moneyline(row)
    spread = _build_spread(row)
    totals = _build_totals(row)
    highlights = _build_highlights(row)
    matchup_board = _build_matchup_board(row, highlights)

    source = board.get("odds_source", "cfbd_lines")
    if source == "none":
        source = "cfbd_lines" if row.get("home_ml") else "none"

    warnings = list(board.get("warnings", []))
    if source == "none" or row.get("home_ml") is None:
        warnings.append(_market_lines_warning(slate_day, use_cache))
    if spread.get("spread_line_source") == "proxy":
        warnings.append("Spread line is a proxy (-7), not a sportsbook close.")
    if board.get("message"):
        warnings.append(str(board["message"]))

    active = board.get("active_moneyline_model") or {}
    feature_cols = None
    try:
        artifact = load_model_artifact()
        feature_cols = list(artifact.get("feature_columns") or [])
    except FileNotFoundError:
        pass

    disclaimer = f"{CFB_DISCLAIMER} betting_ready=false."

    return {
        "game": game,
        "date": slate_day.isoformat(),
        "sport": "cfb",
        "disclaimer": disclaimer,
        "betting_ready": False,
        "warnings": warnings,
        "odds_source": source,
        "active_model": {
            "model_version": active.get("model_version"),
            "feature_set": active.get("feature_set"),
        },
        "moneyline": moneyline,
        "spread": spread,
        "totals": totals,
        "matchup_board": matchup_board,
        "feature_snapshot": _build_feature_snapshot(
            game_id, slate_day, game, feature_cols=feature_cols
        ),
    }
