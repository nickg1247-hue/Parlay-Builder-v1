"""Moneyline, spread, and totals predictions for a CFB slate date."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.cfb_baseline import predict_home_win_proba
from app.models.cfb_margin import PROXY_AWAY_SPREAD, PROXY_HOME_SPREAD, predict_spread_covers
from app.models.cfb_totals import enrich_totals_columns
from app.odds.cfb_betting_lines import resolve_ou_lines_for_slate
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.daily_board import confidence_label
from app.services.schedule_cfb import get_cfb_schedule


def cfb_season_end_year(game_date: date) -> int:
    """CFB season end-year label (2024 season includes Jan 2025 bowls)."""
    return game_date.year if game_date.month >= 8 else game_date.year - 1


def _model_edge_proxy(prob: float) -> float:
    """Distance from coin flip when no market odds are available."""
    return abs(float(prob) - 0.5) * 2.0


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

    ou_by_game, book_lines = resolve_ou_lines_for_slate(df, slate_day)
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
        ml_edge = _model_edge_proxy(prob)
        pick_side = "home" if prob >= 0.5 else "away"
        model_pick = row["home_team"] if pick_side == "home" else row["away_team"]

        payload: dict[str, Any] = {
            "game_id": gid,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "model_prob_home": prob,
            "model_prob_away": round(1.0 - prob, 4),
            "model_pick": model_pick,
            "model_pick_side": pick_side,
            "ml_confidence": confidence_label(ml_edge),
        }

        spread_row = spread_by_id.get(gid)
        if spread_row is not None:
            margin = float(spread_row.model_margin)
            home_cover = float(spread_row.model_prob_home_cover)
            away_cover = float(spread_row.model_prob_away_cover)
            spread_pick, spread_edge = _spread_pick(
                row["home_team"],
                row["away_team"],
                margin,
                home_cover,
                away_cover,
                PROXY_HOME_SPREAD,
            )
            payload.update(
                {
                    "model_margin": round(margin, 1),
                    "model_prob_home_cover": home_cover,
                    "model_prob_away_cover": away_cover,
                    "proxy_home_spread": PROXY_HOME_SPREAD,
                    "spread_pick": spread_pick,
                    "spread_confidence": confidence_label(spread_edge),
                }
            )

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
            payload.update(
                {
                    "expected_total_pts": round(expected, 1),
                    "model_prob_over": prob_over,
                    "ou_line": float(ou_line),
                    "totals_pick": totals_pick,
                    "totals_confidence": confidence_label(totals_edge),
                    "ou_line_source": "book" if gid in book_lines else "matchup",
                }
            )

        out[gid] = payload
    return out


def predict_slate_list(game_date: date | None = None) -> list[dict[str, Any]]:
    return list(predict_slate(game_date).values())
