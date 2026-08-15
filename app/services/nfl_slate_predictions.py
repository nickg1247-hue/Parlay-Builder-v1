"""Moneyline, spread, and totals predictions for an NFL slate date."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.ingest.nfl import is_divisional, normalize_abbr
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nfl_baseline import predict_home_win_proba
from app.models.nfl_margin import PROXY_AWAY_SPREAD, PROXY_HOME_SPREAD, predict_spread_covers
from app.models.nfl_totals import enrich_totals_columns
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.services.daily_board import confidence_label
from app.services.nfl_odds_attach import attach_nfl_odds
from app.services.schedule_nfl import get_nfl_schedule


def nfl_season_year(game_date: date) -> int:
    return game_date.year if game_date.month >= 8 else game_date.year - 1


def _model_edge_proxy(prob: float) -> float:
    return abs(float(prob) - 0.5) * 2.0


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


def _spread_pick(home_team, away_team, model_margin, home_cover, away_cover, home_spread):
    home_edge = home_cover - 0.5
    away_edge = away_cover - 0.5
    if home_edge >= away_edge and home_edge > 0:
        pt_str = f"+{home_spread:g}" if home_spread > 0 else f"{home_spread:g}"
        return f"{home_team} {pt_str}", home_edge * 2.0
    if away_edge > 0:
        away_pt = -home_spread if home_spread != 0 else PROXY_AWAY_SPREAD
        pt_str = f"+{away_pt:g}" if away_pt > 0 else f"{away_pt:g}"
        return f"{away_team} {pt_str}", away_edge * 2.0
    favored = home_team if model_margin >= 0 else away_team
    return f"{favored} by {abs(model_margin):.1f}", _model_edge_proxy(max(home_cover, away_cover))


def _totals_pick(expected: float, prob_over: float, ou_line: float):
    edge = abs(prob_over - 0.5) * 2.0
    if prob_over >= 0.5:
        return f"Over {ou_line:g}", edge
    return f"Under {ou_line:g}", edge


def predict_slate(game_date: date | None = None) -> dict[str, dict[str, Any]]:
    schedule = get_nfl_schedule(game_date, auto_resolve=game_date is None)
    games = schedule.get("games") or []
    if not games:
        return {}

    slate_date = schedule.get("resolved_date") or schedule.get("date")
    slate_day = date.fromisoformat(str(slate_date)[:10])
    season_end = nfl_season_year(slate_day)
    rows = []
    for g in games:
        home_abbr = normalize_abbr(g.get("home_team_abbr"))
        away_abbr = normalize_abbr(g.get("away_team_abbr"))
        game_type = g.get("game_type") or (
            "preseason" if g.get("is_preseason") else "regular"
        )
        rows.append(
            {
                "game_id": str(g["game_id"]),
                "date": slate_date,
                "season": g.get("season") or season_end,
                "home_team": g.get("home_team") or "",
                "away_team": g.get("away_team") or "",
                "home_team_abbr": home_abbr,
                "away_team_abbr": away_abbr,
                "divisional": is_divisional(home_abbr, away_abbr),
                "neutral_site": int(g.get("neutral_site") or 0),
                "game_type": game_type,
                "is_preseason": int(game_type == "preseason"),
                "espn_home_ml": g.get("espn_home_ml") or g.get("home_ml"),
                "espn_away_ml": g.get("espn_away_ml") or g.get("away_ml"),
                "espn_spread": g.get("espn_spread") or g.get("home_spread_point"),
                "espn_ou": g.get("espn_ou") or g.get("ou_line"),
            }
        )

    df = pd.DataFrame(rows)
    df, odds_source = attach_nfl_odds(df, slate_day)

    try:
        probs = predict_home_win_proba(df)
    except FileNotFoundError:
        return {}

    spread_df = None
    try:
        spread_df = predict_spread_covers(df)
    except FileNotFoundError:
        pass
    totals_df = None
    try:
        totals_df = enrich_totals_columns(df)
    except FileNotFoundError:
        pass

    spread_by_id = {}
    if spread_df is not None:
        for row in spread_df.itertuples(index=False):
            spread_by_id[str(row.game_id)] = row
    totals_by_id = {}
    if totals_df is not None:
        for row in totals_df.itertuples(index=False):
            totals_by_id[str(row.game_id)] = row

    out: dict[str, dict[str, Any]] = {}
    for i, row in df.iterrows():
        gid = str(row["game_id"])
        prob = float(probs[i])
        pick_side = "home" if prob >= 0.5 else "away"
        model_pick = row["home_team"] if pick_side == "home" else row["away_team"]
        ml_fields = _ml_market_fields(prob, row.get("home_ml"), row.get("away_ml"))
        payload: dict[str, Any] = {
            "game_id": gid,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "game_type": row.get("game_type") or "regular",
            "model_prob_home": round(prob, 4),
            "model_prob_away": round(1.0 - prob, 4),
            "model_pick": model_pick,
            "model_pick_side": pick_side,
            "ml_confidence": confidence_label(ml_fields["model_edge_ml"]),
            "odds_source": odds_source,
            **ml_fields,
        }
        spread_row = spread_by_id.get(gid)
        if spread_row is not None:
            margin = float(spread_row.model_margin)
            home_cover = float(spread_row.model_prob_home_cover)
            away_cover = float(spread_row.model_prob_away_cover)
            live_sp = row.get("home_spread_point")
            if live_sp is not None and not pd.isna(live_sp):
                home_spread, spread_source = float(live_sp), "book"
            else:
                home_spread, spread_source = PROXY_HOME_SPREAD, "proxy"
            spread_pick, spread_edge = _spread_pick(
                row["home_team"],
                row["away_team"],
                margin,
                home_cover,
                away_cover,
                home_spread,
            )
            payload.update(
                {
                    "model_margin": round(margin, 1),
                    "model_prob_home_cover": home_cover,
                    "model_prob_away_cover": away_cover,
                    "home_spread_point": home_spread,
                    "spread_line_source": spread_source,
                    "spread_pick": spread_pick,
                    "spread_confidence": confidence_label(spread_edge),
                }
            )
        totals_row = totals_by_id.get(gid)
        if totals_row is not None:
            expected = float(totals_row.expected_total_pts)
            prob_over = float(totals_row.model_prob_over)
            ou_line = row.get("ou_line")
            if ou_line is None or pd.isna(ou_line):
                row_ou = getattr(totals_row, "ou_line", None)
                ou_line = float(row_ou) if row_ou is not None and not pd.isna(row_ou) else None
            if ou_line is not None:
                totals_pick, totals_edge = _totals_pick(expected, prob_over, float(ou_line))
                payload.update(
                    {
                        "expected_total_pts": round(expected, 1),
                        "model_prob_over": prob_over,
                        "ou_line": float(ou_line),
                        "totals_pick": totals_pick,
                        "totals_confidence": confidence_label(totals_edge),
                        "ou_line_source": "book" if pd.notna(row.get("ou_line")) else "proxy",
                    }
                )
        out[gid] = payload
    return out


def predict_slate_list(game_date: date | None = None) -> list[dict[str, Any]]:
    return list(predict_slate(game_date).values())
