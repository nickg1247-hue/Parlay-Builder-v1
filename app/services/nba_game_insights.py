"""Per-game NBA insights: moneyline markets + model pick."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_odds_free import load_odds_for_date
from app.odds.nba_odds_repository import get_nba_odds_for_date, has_date
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_repository import last_fetch_meta
from app.odds.team_aliases import is_valid_american_odds
from app.services.daily_board import confidence_label
from app.services.game_insights import _confidence_tier
from app.services.nba_daily_board import NBA_DISCLAIMER, build_nba_daily_board
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


def _nba_sportsbook_lines(
    game: dict[str, Any],
    game_date: date,
    use_cache: bool,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Moneyline only — no model fallbacks."""
    home_team = game["home_team"]
    away_team = game["away_team"]
    empty: dict[str, Any] = {
        "source": "none",
        "away_ml": None,
        "home_ml": None,
    }

    if use_cache:
        odds_df, source = load_odds_for_date(game_date)
        if not odds_df.empty:
            home = normalize_nba_team_name(home_team)
            away = normalize_nba_team_name(away_team)
            row = odds_df[
                (odds_df["home_team"] == home) & (odds_df["away_team"] == away)
            ]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "source": _repo_source_label(source),
                    "home_ml": int(r["home_ml"]),
                    "away_ml": int(r["away_ml"]),
                }
        return empty

    if live_odds_enabled():
        games, repo_source = get_nba_odds_for_date(
            game_date, force_refresh=force_refresh
        )
        match = _match_odds_game(games, home_team, away_team)
        if match:
            home_ml = match.get("home_ml")
            away_ml = match.get("away_ml")
            if is_valid_american_odds(home_ml) and is_valid_american_odds(away_ml):
                return {
                    "source": _repo_source_label(repo_source),
                    "home_ml": int(home_ml),
                    "away_ml": int(away_ml),
                }
        return empty

    if has_date(game_date):
        games, repo_source = get_nba_odds_for_date(game_date, force_refresh=False)
        match = _match_odds_game(games, home_team, away_team)
        if match:
            home_ml = match.get("home_ml")
            away_ml = match.get("away_ml")
            if is_valid_american_odds(home_ml) and is_valid_american_odds(away_ml):
                return {
                    "source": _repo_source_label(repo_source),
                    "home_ml": int(home_ml),
                    "away_ml": int(away_ml),
                }

    return empty


def _build_market_cards(
    lines: dict[str, Any],
    board_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    away: dict[str, Any] = {"moneyline_american": lines["away_ml"]}
    home: dict[str, Any] = {"moneyline_american": lines["home_ml"]}
    if board_row:
        if board_row.get("away_spread_point") is not None:
            away["spread"] = {
                "point": board_row.get("away_spread_point"),
                "american": board_row.get("away_spread_american"),
            }
        if board_row.get("home_spread_point") is not None:
            home["spread"] = {
                "point": board_row.get("home_spread_point"),
                "american": board_row.get("home_spread_american"),
            }
    return {
        "source": lines["source"],
        "away": away,
        "home": home,
    }


def _build_model(board_row: dict[str, Any] | None) -> dict[str, Any]:
    if not board_row:
        return {
            "pick": None,
            "pick_side": None,
            "win_pct": None,
            "edge": None,
            "confidence": None,
            "plus_ev_single": False,
        }

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

    return {
        "pick": pick_team,
        "pick_side": pick_side,
        "win_pct": win_pct,
        "edge": edge,
        "confidence": board_row.get("ml_confidence"),
        "plus_ev_single": board_row.get("plus_ev_single", False),
        "market_prob_home": board_row.get("market_prob_home"),
    }


def _build_highlights(
    model: dict[str, Any],
    board_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pick_side = model.get("pick_side")
    ml_tier = _confidence_tier(model.get("confidence")) if pick_side else None
    highlights: dict[str, Any] = {
        "moneyline_side": pick_side,
        "moneyline_tier": ml_tier,
    }
    if board_row and board_row.get("spread_best_pick"):
        sp = board_row["spread_best_pick"]
        highlights["spread_side"] = sp.get("side")
        highlights["spread_tier"] = _confidence_tier(confidence_label(sp.get("edge")))
    return highlights


def build_nba_game_insights(
    game_id: str,
    game_date: date | None = None,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Merge schedule game, board row, moneyline markets, and model pick."""
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
    )
    board_row = _slate_row(board, game_id)
    model = _build_model(board_row)
    lines = _nba_sportsbook_lines(
        detail["game"],
        game_date,
        use_cache,
        force_refresh=refresh and not use_cache,
    )
    market_cards = _build_market_cards(lines, board_row)
    highlights = _build_highlights(model, board_row)

    warnings = list(board.get("warnings", []))
    meta = last_fetch_meta()
    if meta.get("quota_warning"):
        warnings.append(meta["quota_warning"])
    if market_cards["source"] == "none":
        warnings.append(
            "Market lines unavailable — set USE_LIVE_ODDS=true with ODDS_API_KEY, "
            "import nba_odds_2026.csv, or use demo with use_cache=true."
        )

    spread_disclaimer = board.get("spread_disclaimer")
    if spread_disclaimer:
        warnings.append(spread_disclaimer)

    return {
        "game_id": str(game_id),
        "date": game_date.isoformat(),
        "mode": board.get("mode", "demo" if use_cache else "live"),
        "odds_source": board.get("odds_source", market_cards["source"]),
        "disclaimer": NBA_DISCLAIMER,
        "spread_disclaimer": spread_disclaimer,
        "board_spread_enabled": board.get("board_spread_enabled", False),
        "warnings": warnings,
        "betting_ready": False,
        "game": detail["game"],
        "board_row": board_row,
        "market_cards": market_cards,
        "highlights": highlights,
        "model": model,
        "edge_threshold": board.get("edge_threshold", DEFAULT_MIN_EDGE),
    }
