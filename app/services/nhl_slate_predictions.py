"""Moneyline, puck line, and total predictions for an NHL slate date."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nhl_score_model import predict_matchup
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.services.nhl_ratings import canonical_abbr, load_ratings
from app.services.schedule_nhl import get_nhl_schedule


def _market_fields(prob_home: float, home_ml: Any, away_ml: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "home_ml": None,
        "away_ml": None,
        "market_prob_home": None,
        "market_prob_away": None,
        "model_edge_ml": round(abs(prob_home - 0.5) * 2.0, 4),
        "ev_home": None,
        "ev_away": None,
        "plus_ev_ml": False,
    }
    if home_ml is None or away_ml is None:
        return out
    if not (is_valid_american_odds(home_ml) and is_valid_american_odds(away_ml)):
        return out
    mh, ma = market_probs_from_american(int(home_ml), int(away_ml))
    edge_home = prob_home - mh
    edge_away = (1.0 - prob_home) - ma
    best_edge = max(edge_home, edge_away)
    out.update(
        {
            "home_ml": int(home_ml),
            "away_ml": int(away_ml),
            "market_prob_home": round(mh, 4),
            "market_prob_away": round(ma, 4),
            "model_edge_ml": round(best_edge, 4),
            "ev_home": round(edge_home, 4),
            "ev_away": round(edge_away, 4),
            "plus_ev_ml": best_edge >= DEFAULT_MIN_EDGE,
        }
    )
    if best_edge >= DEFAULT_MIN_EDGE:
        out["ev_pick_team"] = None  # filled by caller once team names are known
        out["_ev_side"] = "home" if edge_home >= edge_away else "away"
    return out


def predict_slate(game_date: date | None = None, *, refresh_ratings: bool = False) -> dict[str, dict[str, Any]]:
    schedule = get_nhl_schedule(
        game_date,
        auto_resolve=game_date is None,
        force_live=False,
    )
    games = schedule.get("games") or []
    if not games:
        return {}

    slate_date = schedule.get("resolved_date") or schedule.get("date")
    slate_day = date.fromisoformat(str(slate_date)[:10])
    ratings = load_ratings(slate_day, force=refresh_ratings)
    teams = ratings.get("teams") or {}
    league = float(ratings.get("league_gf_per_game") or 3.05)
    note = str(ratings.get("ratings_note") or "")

    out: dict[str, dict[str, Any]] = {}
    for game in games:
        home_abbr = canonical_abbr(game.get("home_team_abbr"))
        away_abbr = canonical_abbr(game.get("away_team_abbr"))
        home_ml = game.get("home_ml") if game.get("home_ml") is not None else game.get("espn_home_ml")
        away_ml = game.get("away_ml") if game.get("away_ml") is not None else game.get("espn_away_ml")
        spread = game.get("home_spread_point")
        if spread is None:
            spread = game.get("espn_spread")
        ou = game.get("ou_line")
        if ou is None:
            ou = game.get("espn_ou")
        try:
            spread_f = float(spread) if spread is not None else None
        except (TypeError, ValueError):
            spread_f = None
        try:
            ou_f = float(ou) if ou is not None else None
        except (TypeError, ValueError):
            ou_f = None

        pred = predict_matchup(
            home_team=game.get("home_team") or home_abbr or "Home",
            away_team=game.get("away_team") or away_abbr or "Away",
            home_abbr=home_abbr,
            away_abbr=away_abbr,
            home_rating=teams.get(home_abbr),
            away_rating=teams.get(away_abbr),
            league_gf=league,
            neutral_site=bool(game.get("neutral_site")),
            home_spread=spread_f,
            ou_line=ou_f,
            ratings_note=note,
        )
        market = _market_fields(float(pred["model_prob_home"]), home_ml, away_ml)
        ev_side = market.pop("_ev_side", None)
        if ev_side == "home":
            market["ev_pick_team"] = game.get("home_team")
        elif ev_side == "away":
            market["ev_pick_team"] = game.get("away_team")
        payload = {
            "game_id": str(game.get("game_id")),
            "sport": "nhl",
            "date": slate_date,
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "home_team_abbr": home_abbr,
            "away_team_abbr": away_abbr,
            "game_type": game.get("game_type") or "regular",
            "odds_source": "espn" if home_ml is not None or spread_f is not None or ou_f is not None else "none",
            **pred,
            **market,
        }
        out[str(game.get("game_id"))] = payload
    return out


def predict_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    from app.services.schedule_nhl import get_nhl_game

    detail = get_nhl_game(game_id, game_date)
    if detail is None:
        return None
    resolved = detail.get("resolved_date") or detail.get("date")
    slate_day = date.fromisoformat(str(resolved)[:10]) if resolved else game_date
    preds = predict_slate(slate_day, refresh_ratings=False)
    detail["prediction"] = preds.get(str(game_id))
    detail["model"] = {
        "name": "NHL Poisson score model",
        "family": "nhl_poisson",
        "markets": ["moneyline", "puck_line", "total"],
    }
    return detail
