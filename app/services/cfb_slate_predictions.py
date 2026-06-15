"""Moneyline, spread, and totals predictions for a CFB slate date."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.cfb_baseline import predict_home_win_proba
from app.models.cfb_margin import PROXY_AWAY_SPREAD, PROXY_HOME_SPREAD, predict_spread_covers
from app.models.cfb_totals import enrich_totals_columns
from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.cfb_betting_lines import resolve_lines_for_slate
from app.odds.cfb_team_aliases import normalize_team_name
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.services.cfb_odds_attach import attach_cfb_odds
from app.services.daily_board import confidence_label
from app.services.schedule_cfb import get_cfb_schedule


def cfb_season_end_year(game_date: date) -> int:
    """CFB season end-year label (2024 season includes Jan 2025 bowls)."""
    return game_date.year if game_date.month >= 8 else game_date.year - 1


def _model_edge_proxy(prob: float) -> float:
    """Distance from coin flip when no market odds are available."""
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


def _spread_pick(
    home_team: str,
    away_team: str,
    model_margin: float,
    home_cover: float,
    away_cover: float,
    home_spread: float,
) -> tuple[str | None, float]:
    home_edge = home_cover - 0.5
    away_edge = away_cover - 0.5
    if home_edge >= away_edge and home_edge > 0:
        pt = home_spread
        pt_str = f"+{pt:g}" if pt > 0 else f"{pt:g}"
        return f"{home_team} {pt_str}", home_edge * 2.0
    if away_edge > 0:
        away_pt = -home_spread if home_spread != 0 else PROXY_AWAY_SPREAD
        pt_str = f"+{away_pt:g}" if away_pt > 0 else f"{away_pt:g}"
        return f"{away_team} {pt_str}", away_edge * 2.0
    favored = home_team if model_margin >= 0 else away_team
    margin_abs = abs(model_margin)
    return f"{favored} by {margin_abs:.1f}", _model_edge_proxy(max(home_cover, away_cover))


def _totals_pick(
    expected: float,
    prob_over: float,
    ou_line: float,
) -> tuple[str | None, float]:
    edge = abs(prob_over - 0.5) * 2.0
    if prob_over >= 0.5:
        return f"Over {ou_line:g}", edge
    return f"Under {ou_line:g}", edge


def _resolve_home_spread(
    gid: str,
    cfbd_spread: dict[str, float],
    row: pd.Series,
) -> tuple[float, str]:
    if gid in cfbd_spread:
        return cfbd_spread[gid], "book"
    live_sp = row.get("home_spread_point")
    if live_sp is not None and not pd.isna(live_sp):
        return float(live_sp), "book"
    return PROXY_HOME_SPREAD, "proxy"


def predict_slate(game_date: date | None = None) -> dict[str, dict[str, Any]]:
    schedule = get_cfb_schedule(game_date, auto_resolve=game_date is None)
    games = schedule.get("games") or []
    if not games:
        return {}

    slate_date = schedule.get("resolved_date") or schedule.get("date")
    slate_day = date.fromisoformat(str(slate_date)[:10])
    season_end = cfb_season_end_year(slate_day)
    rows = []
    for g in games:
        rows.append(
            {
                "game_id": str(g["game_id"]),
                "date": slate_date,
                "season": season_end,
                "home_team": normalize_team_name(g.get("home_team") or ""),
                "away_team": normalize_team_name(g.get("away_team") or ""),
            }
        )
    df = pd.DataFrame(rows)

    df, _odds_source = attach_cfb_odds(df, slate_day)
    ou_by_game, spread_by_game, book_ou = resolve_lines_for_slate(df, slate_day)

    if ou_by_game:
        df["ou_line"] = df["game_id"].astype(str).map(ou_by_game)

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

    spread_by_id: dict[str, pd.Series] = {}
    if spread_df is not None:
        for row in spread_df.itertuples(index=False):
            spread_by_id[str(row.game_id)] = row

    totals_by_id: dict[str, pd.Series] = {}
    if totals_df is not None:
        for row in totals_df.itertuples(index=False):
            totals_by_id[str(row.game_id)] = row

    out: dict[str, dict[str, Any]] = {}
    for i, row in df.iterrows():
        gid = str(row["game_id"])
        prob = float(probs[i])
        pick_side = "home" if prob >= 0.5 else "away"
        model_pick = row["home_team"] if pick_side == "home" else row["away_team"]

        ml_fields = _ml_market_fields(
            prob,
            row.get("home_ml"),
            row.get("away_ml"),
        )

        payload: dict[str, Any] = {
            "game_id": gid,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "model_prob_home": prob,
            "model_prob_away": round(1.0 - prob, 4),
            "model_pick": model_pick,
            "model_pick_side": pick_side,
            "ml_confidence": confidence_label(ml_fields["model_edge_ml"]),
            **ml_fields,
        }

        spread_row = spread_by_id.get(gid)
        if spread_row is not None:
            margin = float(spread_row.model_margin)
            home_cover = float(spread_row.model_prob_home_cover)
            away_cover = float(spread_row.model_prob_away_cover)
            home_spread, spread_source = _resolve_home_spread(gid, spread_by_game, row)
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
            if spread_source == "proxy":
                payload["proxy_home_spread"] = PROXY_HOME_SPREAD

        totals_row = totals_by_id.get(gid)
        if totals_row is not None:
            expected = float(totals_row.expected_total_pts)
            prob_over = float(totals_row.model_prob_over)
            ou_line = ou_by_game.get(gid)
            if ou_line is None:
                row_ou = getattr(totals_row, "ou_line", None)
                if row_ou is not None and not pd.isna(row_ou):
                    ou_line = float(row_ou)
            if ou_line is None:
                continue
            totals_pick, totals_edge = _totals_pick(expected, prob_over, float(ou_line))
            ou_source = "book" if gid in book_ou else "matchup"
            if ou_source == "matchup" and pd.notna(row.get("ou_line")):
                ou_source = "book"
            payload.update(
                {
                    "expected_total_pts": round(expected, 1),
                    "model_prob_over": prob_over,
                    "ou_line": float(ou_line),
                    "totals_pick": totals_pick,
                    "totals_confidence": confidence_label(totals_edge),
                    "ou_line_source": ou_source,
                }
            )

        out[gid] = payload
    return out


def predict_slate_list(game_date: date | None = None) -> list[dict[str, Any]]:
    return list(predict_slate(game_date).values())
