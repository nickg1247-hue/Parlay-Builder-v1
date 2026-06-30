"""Per-game insights: markets, model, parlays (Phase C)."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.mlb_odds_free import ODDS_2025_CSV, TOTALS_2025_CSV
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import (
    find_game,
    get_mlb_odds_for_date,
    has_date,
    last_fetch_meta,
)
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.services.daily_board import DAILY_BOARD_CACHE, DISCLAIMER
from app.services.bet_context import build_matchup_form_comparison
from app.services.mlb_game_explanations import build_mlb_game_explanation
from app.services.mlb_team_recent import recent_games_for_matchup
from app.services.schedule_mlb import get_mlb_game

logger = logging.getLogger(__name__)

_INSIGHTS_CACHE_TTL_SECONDS = 300
_insights_cache: dict[str, tuple[float, dict[str, Any]]] = {}

def _pct(prob: float | None) -> float | None:
    if prob is None:
        return None
    return round(prob * 100, 1)


def _load_board(
    game_date: date,
    use_cache: bool,
    refresh: bool,
) -> dict[str, Any]:
    """Read daily board from disk only — never rebuild full slate from game page."""
    cached_board: dict[str, Any] | None = None
    if DAILY_BOARD_CACHE.exists():
        try:
            cached_board = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read daily board cache: %s", exc)
            cached_board = None

    if cached_board and cached_board.get("slate") is not None:
        warnings = list(cached_board.get("warnings") or [])
        if cached_board.get("date") != game_date.isoformat():
            warnings.append(
                "Board cache is from "
                f"{cached_board.get('date')} — run morning refresh for today's picks."
            )
            cached_board = {**cached_board, "warnings": warnings, "stale_board": True}
        elif refresh:
            warnings.append(
                "Showing cached board — full rebuild runs on morning refresh only."
            )
            cached_board = {**cached_board, "warnings": warnings}
        return cached_board

    logger.warning("No daily board cache for game insights on %s", game_date.isoformat())
    return {
        "date": game_date.isoformat(),
        "mode": "demo" if use_cache else "live",
        "odds_source": "none",
        "warnings": [
            "No saved daily board — run morning refresh before opening game pages."
        ],
        "slate": [],
        "top_singles": [],
        "top_parlays": [],
        "edge_threshold": DEFAULT_MIN_EDGE,
    }


def _slate_row(board: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    for row in board.get("slate", []):
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def _parlays_for_game(board: dict[str, Any], game_id: str) -> list[dict[str, Any]]:
    gid = str(game_id)
    out: list[dict[str, Any]] = []
    for parlay in board.get("top_parlays", []):
        legs = parlay.get("legs", [])
        if any(str(leg.get("game_id")) == gid for leg in legs):
            out.append(parlay)
    return out


def _match_event(
    events: list[dict[str, Any]],
    home_team: str,
    away_team: str,
) -> dict[str, Any] | None:
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    for event in events:
        if (
            normalize_team_name(event.get("home_team", "")) == home
            and normalize_team_name(event.get("away_team", "")) == away
        ):
            return event
    return None


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(pd.Series(values).median())


def _median_float(values: list[float]) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).median())


def _parse_h2h_from_event(
    event: dict[str, Any], home_team: str, away_team: str
) -> tuple[int | None, int | None]:
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    home_prices: list[int] = []
    away_prices: list[int] = []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "h2h":
                continue
            prices = {
                normalize_team_name(o["name"]): int(o["price"])
                for o in market.get("outcomes", [])
            }
            if home in prices and away in prices:
                if is_valid_american_odds(prices[home]) and is_valid_american_odds(
                    prices[away]
                ):
                    home_prices.append(prices[home])
                    away_prices.append(prices[away])
    return _median_int(home_prices), _median_int(away_prices)


def _parse_totals_from_event(event: dict[str, Any]) -> tuple[float | None, int | None, int | None]:
    lines: list[float] = []
    over_prices: list[int] = []
    under_prices: list[int] = []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "totals":
                continue
            over_point = None
            over_price = None
            under_price = None
            for outcome in market.get("outcomes", []):
                name = (outcome.get("name") or "").lower()
                if name == "over":
                    over_point = outcome.get("point")
                    over_price = outcome.get("price")
                elif name == "under":
                    under_price = outcome.get("price")
            if (
                over_point is not None
                and over_price is not None
                and under_price is not None
                and is_valid_american_odds(over_price)
                and is_valid_american_odds(under_price)
            ):
                lines.append(float(over_point))
                over_prices.append(int(over_price))
                under_prices.append(int(under_price))
    return _median_float(lines), _median_int(over_prices), _median_int(under_prices)


def _parse_spreads_from_event(
    event: dict[str, Any], home_team: str, away_team: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    home_points: list[float] = []
    home_prices: list[int] = []
    away_points: list[float] = []
    away_prices: list[int] = []
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market.get("key") != "spreads":
                continue
            hp = ap = None
            hpr = apr = None
            for outcome in market.get("outcomes", []):
                team = normalize_team_name(outcome.get("name", ""))
                point = outcome.get("point")
                price = outcome.get("price")
                if point is None or price is None:
                    continue
                if not is_valid_american_odds(price):
                    continue
                if team == home:
                    hp, hpr = float(point), int(price)
                elif team == away:
                    ap, apr = float(point), int(price)
            if hp is not None and hpr is not None:
                home_points.append(hp)
                home_prices.append(hpr)
            if ap is not None and apr is not None:
                away_points.append(ap)
                away_prices.append(apr)

    away_spread = {
        "point": _median_float(away_points),
        "american": _median_int(away_prices),
    }
    home_spread = {
        "point": _median_float(home_points),
        "american": _median_int(home_prices),
    }
    return away_spread, home_spread


def _historical_moneyline(
    home_team: str, away_team: str, game_date: date
) -> tuple[int | None, int | None]:
    if not ODDS_2025_CSV.exists():
        return None, None
    odds = pd.read_csv(ODDS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    row = odds[
        (odds["date"] == game_date.isoformat())
        & (odds["home_team"].map(normalize_team_name) == normalize_team_name(home_team))
        & (odds["away_team"].map(normalize_team_name) == normalize_team_name(away_team))
    ]
    if row.empty:
        return None, None
    r = row.iloc[0]
    home_ml = int(r["home_ml"]) if pd.notna(r.get("home_ml")) else None
    away_ml = int(r["away_ml"]) if pd.notna(r.get("away_ml")) else None
    return home_ml, away_ml


def _historical_totals(
    home_team: str, away_team: str, game_date: date
) -> tuple[float | None, int | None, int | None]:
    if not TOTALS_2025_CSV.exists():
        return None, None, None
    odds = pd.read_csv(TOTALS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    row = odds[
        (odds["date"] == game_date.isoformat())
        & (odds["home_team"].map(normalize_team_name) == normalize_team_name(home_team))
        & (odds["away_team"].map(normalize_team_name) == normalize_team_name(away_team))
    ]
    if row.empty:
        return None, None, None
    r = row.iloc[0]
    line = float(r["ou_line"]) if pd.notna(r.get("ou_line")) else None
    over_am = int(r["over_odds"]) if pd.notna(r.get("over_odds")) else None
    under_am = int(r["under_odds"]) if pd.notna(r.get("under_odds")) else None
    return line, over_am, under_am


def _empty_spread() -> dict[str, Any]:
    return {"point": None, "american": None}


def _repo_source_label(repo_source: str) -> str:
    if repo_source in ("the_odds_api_live", "the_odds_api_historical"):
        return "the_odds_api"
    if repo_source == "csv_import":
        return "historical_cache"
    return repo_source


def _lines_from_repo_game(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": _repo_source_label(source),
        "away_ml": row.get("away_ml"),
        "home_ml": row.get("home_ml"),
        "total_line": row.get("ou_line"),
        "over_am": row.get("over_odds"),
        "under_am": row.get("under_odds"),
        "away_spread": {
            "point": row.get("away_spread_point"),
            "american": row.get("away_spread_american"),
        },
        "home_spread": {
            "point": row.get("home_spread_point"),
            "american": row.get("home_spread_american"),
        },
    }


def _sportsbook_lines(
    game: dict[str, Any],
    game_date: date,
    use_cache: bool,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Sportsbook lines only — no model fallbacks."""
    home_team = game["home_team"]
    away_team = game["away_team"]
    empty = {
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
        if has_date(game_date):
            games, repo_source = get_mlb_odds_for_date(game_date)
            row = find_game(games or [], home_team, away_team)
            if row:
                return _lines_from_repo_game(row, repo_source)
        home_ml, away_ml = _historical_moneyline(home_team, away_team, game_date)
        total_line, over_am, under_am = _historical_totals(
            home_team, away_team, game_date
        )
        if home_ml is not None or total_line is not None:
            return {
                **empty,
                "source": "historical_cache",
                "away_ml": away_ml,
                "home_ml": home_ml,
                "total_line": total_line,
                "over_am": over_am,
                "under_am": under_am,
            }
        return empty

    if live_odds_enabled():
        games, repo_source = get_mlb_odds_for_date(
            game_date,
            force_refresh=force_refresh,
            include_totals=True,
            include_spreads=True,
        )
        row = find_game(games or [], home_team, away_team)
        if row:
            return _lines_from_repo_game(row, repo_source)
        return empty

    if has_date(game_date):
        games, repo_source = get_mlb_odds_for_date(game_date)
        row = find_game(games or [], home_team, away_team)
        if row:
            return _lines_from_repo_game(row, repo_source)

    return empty


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


def _confidence_tier(label: str | None) -> str | None:
    """Map daily-board confidence label to highlight tier (low/medium/high)."""
    if not label or label == "—":
        return None
    key = label.strip().lower()
    if key in ("lean only", "blocked (stale data)"):
        return None
    if key == "low":
        return "low"
    if key in ("moderate", "medium"):
        return "medium"
    if key in ("high", "very high", "extremely high"):
        return "high"
    return None


def _win_pct_highlight_tier(
    pick_prob: float | None,
    confidence_label: str | None = None,
) -> str | None:
    """Map model win probability to market highlight tier (not EV/edge)."""
    if pick_prob is not None:
        p = float(pick_prob)
        if p < 0.54:
            return None
        if p < 0.58:
            return "low"
        if p < 0.62:
            return "medium"
        if p < 0.67:
            return "high"
        return "high"
    return _confidence_tier(confidence_label)


def _build_highlights(
    model: dict[str, Any],
    board_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pick_side = model.get("pick_side")
    pick_prob = None
    if board_row:
        pick_prob = board_row.get("model_confidence_prob")
    if pick_prob is None and model.get("win_pct") is not None:
        pick_prob = float(model["win_pct"]) / 100.0

    win_label = model.get("win_confidence") or model.get("confidence")
    ml_tier = (
        _win_pct_highlight_tier(pick_prob, win_label) if pick_side else None
    )

    totals_pick = (model.get("totals_pick") or "").strip().lower()
    total_side = None
    if totals_pick.startswith("over"):
        total_side = "over"
    elif totals_pick.startswith("under"):
        total_side = "under"

    return {
        "moneyline_side": pick_side,
        "moneyline_tier": ml_tier,
        "total_side": total_side,
        "total_tier": None,
        "spread_side": None,
        "spread_tier": None,
    }


def _build_model(board_row: dict[str, Any] | None) -> dict[str, Any]:
    if not board_row:
        return {
            "pick": None,
            "pick_side": None,
            "win_pct": None,
            "expected_runs": None,
            "edge": None,
            "confidence": None,
            "totals_pick": None,
            "total_edge": None,
            "totals_confidence": None,
            "ev_pick": None,
            "ev_edge": None,
        }

    prob_home = board_row.get("model_prob_home")
    pick_side = board_row.get("model_pick_side")
    if pick_side:
        pick_team = board_row.get("model_pick_team")
    elif prob_home is not None:
        pick_side = "home" if float(prob_home) >= 0.5 else "away"
        pick_team = (
            board_row["home_team"] if pick_side == "home" else board_row["away_team"]
        )
    else:
        pick_side = None
        pick_team = None

    data_stale = bool(board_row.get("prediction_data_stale")) and (
        board_row.get("model_win_pct_display") is None
    )
    display_pct = board_row.get("model_win_pct_display")
    if data_stale:
        win_pct = None
    elif pick_side == "home" and prob_home is not None:
        win_pct = (
            display_pct
            if display_pct is not None
            else _pct(prob_home)
        )
    elif pick_side == "away" and prob_home is not None:
        win_pct = (
            display_pct
            if display_pct is not None
            else _pct(1.0 - float(prob_home))
        )
    elif pick_side == "home":
        win_pct = display_pct if display_pct is not None else _pct(board_row.get("display_prob_home"))
    elif pick_side == "away":
        dh = board_row.get("display_prob_home")
        win_pct = (
            display_pct
            if display_pct is not None
            else (_pct(1.0 - float(dh)) if dh is not None else None)
        )
    else:
        win_pct = None

    market_home = board_row.get("market_prob_home")
    if pick_side == "home" and prob_home is not None and market_home is not None:
        edge = float(prob_home) - float(market_home)
    elif pick_side == "away" and prob_home is not None and market_home is not None:
        edge = (1.0 - float(prob_home)) - (1.0 - float(market_home))
    else:
        edge = board_row.get("ml_edge_best")

    confidence = board_row.get("model_confidence")
    ev_confidence = board_row.get("ml_confidence")

    return {
        "pick": pick_team,
        "pick_side": pick_side,
        "win_pct": win_pct,
        "win_pct_suppressed": data_stale,
        "expected_runs": board_row.get("expected_total_runs"),
        "edge": edge,
        "confidence": confidence,
        "win_confidence": confidence,
        "ev_confidence": ev_confidence,
        "totals_pick": board_row.get("totals_pick"),
        "total_edge": board_row.get("total_edge"),
        "totals_confidence": board_row.get("totals_confidence"),
        "plus_ev_single": board_row.get("plus_ev_single", False),
        "plus_ev_total": board_row.get("plus_ev_total", False),
        "ev_pick": board_row.get("ev_pick_team"),
        "ev_edge": board_row.get("ev_pick_edge"),
    }


def build_game_insights(
    game_id: str,
    game_date: date | None = None,
    use_cache: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Merge schedule game, daily board row, markets, model, and parlays."""
    game_date = game_date or date.today()

    detail = get_mlb_game(game_id, game_date)
    if detail is None:
        return None

    try:
        resolved_date = date.fromisoformat(str(detail.get("date") or game_date.isoformat()))
    except ValueError:
        resolved_date = game_date
    cache_key = f"{game_id}:{resolved_date.isoformat()}:{use_cache}"
    if not refresh:
        hit = _insights_cache.get(cache_key)
        if hit and (time.time() - hit[0]) < _INSIGHTS_CACHE_TTL_SECONDS:
            return hit[1]

    board = _load_board(resolved_date, use_cache=use_cache, refresh=refresh)
    board_row = _slate_row(board, game_id) or detail.get("board_row")
    model = _build_model(board_row)
    lines = _sportsbook_lines(
        detail["game"], resolved_date, use_cache, force_refresh=False
    )
    market_cards = _build_market_cards(lines)
    highlights = _build_highlights(model, board_row)
    try:
        explanation = build_mlb_game_explanation(
            game_id,
            resolved_date,
            board_row,
            use_cache=use_cache,
        )
    except Exception as exc:
        logger.warning("Game explanation skipped for %s: %s", game_id, exc)
        explanation = None
    recent_games = recent_games_for_matchup(
        detail["game"]["home_team"],
        detail["game"]["away_team"],
        resolved_date,
    )
    form_comparison = build_matchup_form_comparison(
        detail["game"]["home_team"],
        detail["game"]["away_team"],
        game_date,
        model_pick_side=board_row.get("model_pick_side") if board_row else None,
    )

    warnings = list(board.get("warnings", []))
    meta = last_fetch_meta()
    if meta.get("quota_warning"):
        warnings.append(meta["quota_warning"])
    if market_cards["source"] == "none":
        warnings.append(
            "Market lines unavailable — set USE_LIVE_ODDS=true with ODDS_API_KEY "
            "or use demo date with use_cache=true."
        )

    payload = {
        "game_id": str(game_id),
        "date": resolved_date.isoformat(),
        "requested_date": game_date.isoformat(),
        "mode": board.get("mode", "demo" if use_cache else "live"),
        "odds_source": board.get("odds_source", "none"),
        "disclaimer": DISCLAIMER,
        "warnings": warnings,
        "game": detail["game"],
        "board_row": board_row,
        "market_cards": market_cards,
        "highlights": highlights,
        "model": model,
        "form_comparison": form_comparison,
        "explanation": explanation,
        "recent_games": recent_games,
        "parlays": _parlays_for_game(board, game_id),
        "edge_threshold": board.get("edge_threshold", DEFAULT_MIN_EDGE),
        "prediction_freshness": board.get("prediction_freshness"),
    }
    if not refresh:
        _insights_cache[cache_key] = (time.time(), payload)
    return payload
