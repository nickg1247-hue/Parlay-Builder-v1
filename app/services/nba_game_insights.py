"""Per-game NBA insights: moneyline, spread, and totals markets + model."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_odds_free import load_odds_for_date
from app.odds.nba_odds_repository import get_nba_odds_for_date, has_date
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_repository import last_fetch_meta
from app.odds.team_aliases import is_valid_american_odds
from app.services.daily_board import confidence_label
from app.services.game_insights import _confidence_tier
from app.services.nba_daily_board import (
    NBA_DISCLAIMER,
    _nba_season_end_year,
    build_nba_daily_board,
)
from app.services.nba_prediction_detail import build_game_prediction_detail
from app.services.schedule_nba import get_nba_game

logger = logging.getLogger(__name__)

LIVE_ODDS_SOURCES = frozenset({"the_odds_api", "the_odds_api_live"})


def _pct(prob: float | None) -> float | None:
    if prob is None:
        return None
    return round(prob * 100, 1)


def _slate_row(board: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    for row in board.get("slate", []):
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def _fallback_board_row(game: dict[str, Any], game_date: date) -> dict[str, Any] | None:
    """Single-game model row when slate lookup misses (model works without odds)."""
    try:
        from app.models.nba_baseline import predict_home_win_proba
    except ImportError:
        return None

    try:
        season_end = _nba_season_end_year(game_date)
        df = pd.DataFrame(
            [
                {
                    "game_id": str(game["game_id"]),
                    "date": game_date.isoformat(),
                    "season": season_end,
                    "home_team": normalize_nba_team_name(game["home_team"]),
                    "away_team": normalize_nba_team_name(game["away_team"]),
                }
            ]
        )
        model_home = float(predict_home_win_proba(df)[0])
    except FileNotFoundError:
        return None

    pick_side = "home" if model_home >= 0.5 else "away"
    pick_team = game["home_team"] if pick_side == "home" else game["away_team"]
    return {
        "game_id": str(game["game_id"]),
        "home_team": game["home_team"],
        "away_team": game["away_team"],
        "model_prob_home": round(model_home, 4),
        "ml_edge_best": None,
        "ml_confidence": confidence_label(None),
        "plus_ev_single": False,
        "best_pick": None,
        "pick_side": pick_side,
        "pick": pick_team,
    }


def _match_odds_game(
    games: list[dict[str, Any]] | None,
    home_team: str,
    away_team: str,
) -> dict[str, Any] | None:
    home = normalize_nba_team_name(home_team)
    away = normalize_nba_team_name(away_team)
    for game in games or []:
        if (
            normalize_nba_team_name(game.get("home_team", "")) == home
            and normalize_nba_team_name(game.get("away_team", "")) == away
        ):
            return game
    return None


def _repo_source_label(repo_source: str) -> str:
    if repo_source in LIVE_ODDS_SOURCES:
        return "the_odds_api"
    if repo_source in ("csv_import", "historical_cache", "repository"):
        return repo_source
    return repo_source


def _empty_spread() -> dict[str, Any]:
    return {"point": None, "american": None}


def _lines_from_match(match: dict[str, Any], source: str) -> dict[str, Any]:
    home_ml = match.get("home_ml")
    away_ml = match.get("away_ml")
    return {
        "source": _repo_source_label(source),
        "home_ml": int(home_ml) if is_valid_american_odds(home_ml) else None,
        "away_ml": int(away_ml) if is_valid_american_odds(away_ml) else None,
        "total_line": (
            float(match["ou_line"])
            if match.get("ou_line") is not None
            else None
        ),
        "over_am": (
            int(match["over_odds"])
            if is_valid_american_odds(match.get("over_odds"))
            else None
        ),
        "under_am": (
            int(match["under_odds"])
            if is_valid_american_odds(match.get("under_odds"))
            else None
        ),
        "away_spread": {
            "point": match.get("away_spread_point"),
            "american": match.get("away_spread_american"),
        },
        "home_spread": {
            "point": match.get("home_spread_point"),
            "american": match.get("home_spread_american"),
        },
    }


def _lines_from_csv_row(row: pd.Series, source: str) -> dict[str, Any]:
    lines = {
        "source": _repo_source_label(source),
        "home_ml": int(row["home_ml"]),
        "away_ml": int(row["away_ml"]),
        "total_line": None,
        "over_am": None,
        "under_am": None,
        "away_spread": _empty_spread(),
        "home_spread": _empty_spread(),
    }
    if "ou_line" in row.index and pd.notna(row.get("ou_line")):
        lines["total_line"] = float(row["ou_line"])
        if is_valid_american_odds(row.get("over_odds")):
            lines["over_am"] = int(row["over_odds"])
        if is_valid_american_odds(row.get("under_odds")):
            lines["under_am"] = int(row["under_odds"])
    return lines


def _lines_from_cached_date(
    home_team: str,
    away_team: str,
    game_date: date,
) -> dict[str, Any] | None:
    """CSV + repository snapshot for a date (never calls The Odds API)."""
    odds_df, source = load_odds_for_date(game_date)
    if odds_df.empty:
        return None
    home = normalize_nba_team_name(home_team)
    away = normalize_nba_team_name(away_team)
    row = odds_df[(odds_df["home_team"] == home) & (odds_df["away_team"] == away)]
    if row.empty:
        return None
    return _lines_from_csv_row(row.iloc[0], source)


def _market_lines_warning(game_date: date, use_cache: bool) -> str:
    """Context-specific hint when sportsbook lines are missing."""
    from app.odds.nba_odds_free import ODDS_2026_CSV

    iso = game_date.isoformat()
    today = date.today()
    csv_hint = (
        "python scripts/load_nba_odds_free.py your_file.csv"
        if not ODDS_2026_CSV.exists()
        else f"ensure {iso} is in data/processed/nba_odds_2026.csv"
    )

    if use_cache:
        return (
            f"No cached odds for {iso}. Import: {csv_hint}, "
            "or capture live lines on /nba/board → Run live."
        )

    if not live_odds_enabled():
        return (
            "Live odds disabled (USE_LIVE_ODDS=false) — model picks only. "
            "For today's sportsbook lines: set USE_LIVE_ODDS=true and ODDS_API_KEY in .env, "
            "restart the server, open /nba/board → Run live. "
            f"Offline demo: {csv_hint}, then open the game with "
            f"?use_cache=true&date={iso} (demo slate: date=2026-04-10)."
        )

    if game_date < today:
        return (
            f"No odds snapshot for {iso}. Import: {csv_hint}, "
            f"or use ?use_cache=true&date={iso} after import."
        )

    return (
        "No lines for this date yet. Open /nba/board → Run live (requires "
        "USE_LIVE_ODDS=true + ODDS_API_KEY). Check quota if the board also shows no odds."
    )


def _nba_sportsbook_lines(
    game: dict[str, Any],
    game_date: date,
    use_cache: bool,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Sportsbook lines only — no model fallbacks."""
    home_team = game["home_team"]
    away_team = game["away_team"]
    empty: dict[str, Any] = {
        "source": "none",
        "away_ml": None,
        "home_ml": None,
        "total_line": None,
        "over_am": None,
        "under_am": None,
        "away_spread": _empty_spread(),
        "home_spread": _empty_spread(),
    }

    if use_cache:
        cached = _lines_from_cached_date(home_team, away_team, game_date)
        return cached or empty

    if live_odds_enabled():
        games, repo_source = get_nba_odds_for_date(
            game_date,
            force_refresh=force_refresh,
            include_spreads=True,
            include_totals=True,
        )
        match = _match_odds_game(games, home_team, away_team)
        if match:
            return _lines_from_match(match, repo_source)
        cached = _lines_from_cached_date(home_team, away_team, game_date)
        return cached or empty

    if has_date(game_date):
        games, repo_source = get_nba_odds_for_date(
            game_date,
            force_refresh=False,
            include_spreads=True,
            include_totals=True,
        )
        match = _match_odds_game(games, home_team, away_team)
        if match:
            return _lines_from_match(match, repo_source)

    cached = _lines_from_cached_date(home_team, away_team, game_date)
    return cached or empty


def _build_market_cards(lines: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": lines["source"],
        "away": {
            "moneyline_american": lines["away_ml"],
            "spread": lines["away_spread"],
        },
        "home": {
            "moneyline_american": lines["home_ml"],
            "spread": lines["home_spread"],
        },
        "total": {
            "line": lines["total_line"],
            "over_american": lines["over_am"],
            "under_american": lines["under_am"],
        },
    }


def _format_spread_pick(spread_best: dict[str, Any] | None) -> str | None:
    if not spread_best:
        return None
    team = spread_best.get("team")
    point = spread_best.get("spread_point")
    if team is None:
        return None
    if point is None:
        return team
    sign = "+" if float(point) > 0 else ""
    pt = float(point)
    if pt == int(pt):
        return f"{team} {sign}{int(pt)}"
    return f"{team} {sign}{pt:.1f}"


def _build_model(board_row: dict[str, Any] | None) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "pick": None,
        "pick_side": None,
        "win_pct": None,
        "edge": None,
        "confidence": None,
        "plus_ev_single": False,
        "model_margin": None,
        "spread_pick": None,
        "spread_edge": None,
        "model_prob_home_cover": None,
        "model_prob_away_cover": None,
        "model_total_pts": None,
        "prob_over": None,
        "totals_pick": None,
        "total_edge": None,
        "totals_confidence": None,
        "plus_ev_total": False,
    }
    if not board_row:
        return empty

    best = board_row.get("best_pick")
    if best:
        pick_team = best.get("team")
        pick_side = best.get("side")
        edge = best.get("edge")
    else:
        pick_side = (
            "home" if board_row.get("model_prob_home", 0.5) >= 0.5 else "away"
        )
        pick_team = (
            board_row["home_team"] if pick_side == "home" else board_row["away_team"]
        )
        edge = board_row.get("ml_edge_best")

    if pick_side == "home":
        win_pct = _pct(board_row.get("model_prob_home"))
    else:
        home_prob = board_row.get("model_prob_home")
        win_pct = _pct(1.0 - float(home_prob)) if home_prob is not None else None

    spread_best = board_row.get("spread_best_pick")
    spread_pick = _format_spread_pick(spread_best)
    spread_edge = spread_best.get("edge") if spread_best else None

    return {
        "pick": pick_team,
        "pick_side": pick_side,
        "win_pct": win_pct,
        "edge": edge,
        "confidence": board_row.get("ml_confidence"),
        "plus_ev_single": board_row.get("plus_ev_single", False),
        "market_prob_home": board_row.get("market_prob_home"),
        "model_margin": board_row.get("model_margin"),
        "spread_pick": spread_pick,
        "spread_edge": spread_edge,
        "model_prob_home_cover": board_row.get("model_prob_home_cover"),
        "model_prob_away_cover": board_row.get("model_prob_away_cover"),
        "model_total_pts": board_row.get("expected_total_pts"),
        "prob_over": board_row.get("model_prob_over"),
        "totals_pick": board_row.get("totals_pick"),
        "total_edge": board_row.get("total_edge"),
        "totals_confidence": board_row.get("totals_confidence"),
        "plus_ev_total": board_row.get("plus_ev_total", False),
    }


def _build_highlights(
    model: dict[str, Any],
    board_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pick_side = model.get("pick_side")
    ml_tier = _confidence_tier(model.get("confidence")) if pick_side else None

    totals_pick = (model.get("totals_pick") or "").strip().lower()
    total_side = None
    if totals_pick.startswith("over"):
        total_side = "over"
    elif totals_pick.startswith("under"):
        total_side = "under"
    totals_tier = (
        _confidence_tier(model.get("totals_confidence")) if total_side else None
    )

    highlights: dict[str, Any] = {
        "moneyline_side": pick_side,
        "moneyline_tier": ml_tier,
        "total_side": total_side,
        "total_tier": totals_tier,
        "spread_side": None,
        "spread_tier": None,
    }

    if board_row and board_row.get("spread_best_pick"):
        sp = board_row["spread_best_pick"]
        highlights["spread_side"] = sp.get("side")
        highlights["spread_tier"] = _confidence_tier(
            confidence_label(sp.get("edge"))
        )

    return highlights


def _lines_from_board_row(
    board_row: dict[str, Any] | None,
    source: str = "demo_synthetic",
) -> dict[str, Any] | None:
    if not board_row or board_row.get("home_ml") is None:
        return None
    return {
        "source": source,
        "home_ml": board_row["home_ml"],
        "away_ml": board_row["away_ml"],
        "total_line": board_row.get("ou_line"),
        "over_am": board_row.get("over_odds"),
        "under_am": board_row.get("under_odds"),
        "away_spread": {
            "point": board_row.get("away_spread_point"),
            "american": board_row.get("away_spread_american"),
        },
        "home_spread": {
            "point": board_row.get("home_spread_point"),
            "american": board_row.get("home_spread_american"),
        },
    }


def build_nba_game_insights(
    game_id: str,
    game_date: date | None = None,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Merge schedule game, board row, markets, and model (ML + spread + totals)."""
    game_date = game_date or date.today()
    detail = get_nba_game(game_id, game_date)
    if detail is None:
        return None

    board = build_nba_daily_board(
        game_date=game_date,
        min_edge=DEFAULT_MIN_EDGE,
        force_refresh=refresh and not use_cache,
        use_cache=use_cache,
        log_clv=False,
        skip_totals=False,
    )
    board_row = _slate_row(board, game_id)
    if board_row is None and not board.get("error"):
        board_row = _fallback_board_row(detail["game"], game_date)

    model = _build_model(board_row)
    lines = _lines_from_board_row(
        board_row,
        source=board.get("odds_source", "demo_synthetic"),
    )
    if lines is None:
        lines = _nba_sportsbook_lines(
            detail["game"],
            game_date,
            use_cache,
            force_refresh=refresh and not use_cache,
        )
    market_cards = _build_market_cards(lines)
    highlights = _build_highlights(model, board_row)
    prediction = build_game_prediction_detail(detail["game"], game_date)

    warnings = list(board.get("warnings", []))
    meta = last_fetch_meta()
    if meta.get("quota_warning"):
        warnings.append(meta["quota_warning"])
    if market_cards["source"] == "none":
        warnings.append(_market_lines_warning(game_date, use_cache))
    elif board.get("demo_synthetic_odds"):
        warnings.append(
            "Demo uses a fixed benchmark market (54% home / 224.5 total), not real closing odds."
        )

    spread_disclaimer = board.get("spread_disclaimer")
    if spread_disclaimer:
        warnings.append(spread_disclaimer)
    totals_disclaimer = board.get("totals_disclaimer")
    if totals_disclaimer:
        warnings.append(totals_disclaimer)

    return {
        "game_id": str(game_id),
        "date": game_date.isoformat(),
        "mode": board.get("mode", "demo" if use_cache else "live"),
        "odds_source": board.get("odds_source", market_cards["source"]),
        "disclaimer": NBA_DISCLAIMER,
        "spread_disclaimer": spread_disclaimer,
        "totals_disclaimer": totals_disclaimer,
        "board_spread_enabled": board.get("board_spread_enabled", False),
        "board_totals_enabled": board.get("board_totals_enabled", False),
        "warnings": warnings,
        "betting_ready": False,
        "game": detail["game"],
        "board_row": board_row,
        "market_cards": market_cards,
        "highlights": highlights,
        "model": model,
        "prediction": prediction,
        "edge_threshold": board.get("edge_threshold", DEFAULT_MIN_EDGE),
    }
