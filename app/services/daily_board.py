"""Build daily MLB dashboard payload for the web UI."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.market_status import get_market_eval_status
from app.db.mlb_status import get_mlb_data_status
from app.db.parlay_status import get_parlay_status
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.models.calibration import (
    blend_display_prob,
    model_disagrees_heavy_favorite,
)
from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import (
    DEFAULT_MAX_PARLAYS,
    attach_market_odds,
    rank_parlays,
    _candidate_legs,
)
from app.parlay.slate import build_slate_dataframe, build_slate_from_history
from app.parlay.totals_slate import build_totals_slate

logger = logging.getLogger(__name__)

DAILY_BOARD_CACHE = PROJECT_ROOT / "data" / "processed" / "daily_board.json"
DISCLAIMER = (
    "Experimental analytics — not betting advice. EV signals are not validated. "
    "Totals model is separate from moneyline."
)


def _sanitize_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, (np.floating,)) and np.isnan(obj):
        return None
    return obj


def _has_odds_api_key() -> bool:
    return bool(os.getenv("ODDS_API_KEY", "").strip())


def _build_slate(game_date: date, use_cache: bool) -> pd.DataFrame:
    if use_cache:
        slate = build_slate_from_history(game_date)
        if not slate.empty:
            return slate
    return build_slate_dataframe(game_date)


def _top_totals(slate: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    picks = [
        {
            "matchup": g["matchup"],
            "pick": g["totals_pick"],
            "ou_line": g["ou_line"],
            "expected_total_runs": g["expected_total_runs"],
            "edge": g["total_edge"],
        }
        for g in slate
        if g.get("plus_ev_total") and g.get("totals_pick") and g.get("total_edge") is not None
    ]
    picks.sort(key=lambda p: p["edge"], reverse=True)
    return picks[:limit]


def _slate_rows(
    merged: pd.DataFrame,
    has_odds: bool,
    totals_by_game: dict[str, dict[str, Any]],
    min_edge: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        matchup = f"{row.away_team} @ {row.home_team}"
        model_home = float(row.model_prob_home)
        market_home = None
        edge_home = None
        plus_ev = False
        best_pick = None

        if has_odds and pd.notna(getattr(row, "home_ml", None)):
            if is_valid_american_odds(row.home_ml) and is_valid_american_odds(
                row.away_ml
            ):
                market_home, market_away = market_probs_from_american(
                    int(row.home_ml), int(row.away_ml)
                )
                model_away = float(row.model_prob_away)
                edge_home = model_home - market_home
                edge_away = model_away - market_away
                options = [
                    ("home", row.home_team, edge_home, int(row.home_ml)),
                    ("away", row.away_team, edge_away, int(row.away_ml)),
                ]
                side, team, edge, am = max(options, key=lambda x: x[2])
                if edge >= min_edge:
                    plus_ev = True
                    best_pick = {
                        "side": side,
                        "team": team,
                        "edge": round(edge, 4),
                        "american_odds": am,
                    }

        display_home = blend_display_prob(model_home, market_home)
        disagree = model_disagrees_heavy_favorite(model_home, market_home)

        totals = totals_by_game.get(str(row.game_id), {})
        ou_line = totals.get("ou_line")
        if ou_line is not None and pd.isna(ou_line):
            ou_line = None
        exp_runs = totals.get("expected_total_runs")
        rows.append(
            {
                "game_id": str(row.game_id),
                "matchup": matchup,
                "away_team": row.away_team,
                "home_team": row.home_team,
                "model_prob_home": round(model_home, 4),
                "display_prob_home": round(display_home, 4),
                "market_prob_home": round(market_home, 4) if market_home else None,
                "edge_home": round(edge_home, 4) if edge_home is not None else None,
                "plus_ev_single": plus_ev,
                "best_pick": best_pick,
                "model_disagrees_heavy_favorite": disagree,
                "ou_line": ou_line,
                "expected_total_runs": (
                    round(float(exp_runs), 2) if exp_runs is not None else None
                ),
                "totals_pick": totals.get("pick"),
                "model_prob_over": totals.get("model_prob_over"),
                "market_prob_over": totals.get("market_prob_over"),
                "total_edge": totals.get("total_edge"),
                "plus_ev_total": totals.get("plus_ev_total", False),
            }
        )
    return rows


def _totals_by_game(
    game_date: date,
    use_cache: bool,
    slate_df: pd.DataFrame,
    attach_market_odds: bool = True,
) -> dict[str, dict[str, Any]]:
    try:
        totals_df = build_totals_slate(
            game_date,
            use_cache=use_cache,
            moneyline_slate=slate_df,
            attach_market_odds=attach_market_odds,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.warning("Totals slate skipped: %s", exc)
        return {}
    if totals_df.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in totals_df.itertuples(index=False):
        ou = getattr(r, "ou_line", None)
        if ou is not None and pd.isna(ou):
            ou = None
        out[str(r.game_id)] = {
            "ou_line": ou,
            "expected_total_runs": getattr(r, "expected_total_runs", None),
            "pick": getattr(r, "pick", None),
            "model_prob_over": getattr(r, "model_prob_over", None),
            "market_prob_over": getattr(r, "market_prob_over", None),
            "total_edge": getattr(r, "total_edge", None),
            "plus_ev_total": getattr(r, "plus_ev_total", False),
        }
    return out


def _top_singles(slate: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    picks = []
    for game in slate:
        if game.get("best_pick"):
            picks.append(
                {
                    "matchup": game["matchup"],
                    "team": game["best_pick"]["team"],
                    "side": game["best_pick"]["side"],
                    "edge": game["best_pick"]["edge"],
                    "american_odds": game["best_pick"]["american_odds"],
                    "model_prob": (
                        game["model_prob_home"]
                        if game["best_pick"]["side"] == "home"
                        else round(1 - game["model_prob_home"], 4)
                    ),
                }
            )
    picks.sort(key=lambda p: p["edge"], reverse=True)
    return picks[:limit]


def _top_parlays_payload(
    merged: pd.DataFrame,
    odds_source: str,
    min_edge: float,
    max_parlays: int,
) -> list[dict[str, Any]]:
    if odds_source == "none":
        return []
    with_odds = merged[merged["home_ml"].notna()].copy()
    legs = _candidate_legs(with_odds)
    parlays = rank_parlays(
        legs,
        min_edge=min_edge,
        max_parlays=max_parlays,
    )
    return [
        {
            "num_legs": p.num_legs,
            "ev": round(p.ev, 4),
            "ev_pct": f"{p.ev:.1%}",
            "model_joint_prob": round(p.model_joint_prob, 4),
            "market_joint_prob": round(p.market_joint_prob, 4),
            "decimal_payout": round(p.decimal_payout, 2),
            "legs": [asdict(leg) for leg in p.legs],
        }
        for p in parlays
    ]


def _status_footer() -> dict[str, Any]:
    conn = get_connection()
    try:
        mlb = get_mlb_data_status(conn)
    finally:
        conn.close()
    return {
        **mlb,
        **get_market_eval_status(),
        **get_parlay_status(),
    }


def build_daily_board(
    game_date: date | None = None,
    use_cache: bool = False,
    refresh: bool = False,
    skip_totals: bool | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = DEFAULT_MAX_PARLAYS,
) -> dict[str, Any]:
    game_date = game_date or date.today()
    # Live board defaults to fast path; pass skip_totals=false to include O/U model.
    if skip_totals is None:
        skip_totals = not use_cache
    cache_key = (
        f"{game_date.isoformat()}_{'cache' if use_cache else 'live'}"
        f"_{'no_totals' if skip_totals else 'totals'}"
        f"_edge{min_edge}_parlays{max_parlays}"
    )

    if not refresh and DAILY_BOARD_CACHE.exists():
        cached = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
        if cached.get("cache_key") == cache_key:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(
                cached["generated_at"].replace("Z", "+00:00")
            )
            if age.total_seconds() < 300:
                return cached

    warnings: list[str] = []
    if not use_cache and not _has_odds_api_key():
        warnings.append(
            "ODDS_API_KEY not set. Add your free key to .env — see DEV.md. "
            "Showing model-only slate until odds are available."
        )

    slate_df = _build_slate(game_date, use_cache)
    if slate_df.empty:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_key": cache_key,
            "date": game_date.isoformat(),
            "mode": "demo" if use_cache else "live",
            "disclaimer": DISCLAIMER,
            "warnings": warnings,
            "error": "No MLB games scheduled for this date.",
            "odds_source": "none",
            "slate": [],
            "top_singles": [],
            "top_parlays": [],
            "top_totals": [],
            "status": _status_footer(),
        }
        payload = _sanitize_json(payload)
        _write_cache(payload)
        return payload

    merged, odds_source = attach_market_odds(slate_df, game_date, use_cache=use_cache)
    if odds_source == "none":
        warnings.append(
            "Odds unavailable — slate shows model probabilities only. "
            "Check ODDS_API_KEY or use ?use_cache=true for historical demo."
        )

    has_odds = odds_source != "none"
    # Always score model runs; attach sportsbook O/U when skip_totals is false.
    totals_by_game = _totals_by_game(
        game_date,
        use_cache,
        slate_df,
        attach_market_odds=not skip_totals,
    )
    if skip_totals:
        warnings.append(
            "O/U unchecked: showing model runs only. Check O/U and Refresh for "
            "sportsbook lines, picks, and total edge."
        )
    elif not totals_by_game:
        warnings.append(
            "Totals model unavailable — O/U columns empty. Run train_mlb_totals.py."
        )
    else:
        with_line = sum(1 for t in totals_by_game.values() if t.get("ou_line") is not None)
        if with_line == 0 and not skip_totals:
            warnings.append(
                "No sportsbook O/U lines matched this slate. Model runs still shown."
            )

    slate = _slate_rows(merged, has_odds, totals_by_game, min_edge)
    top_singles = _top_singles(slate) if has_odds else []
    top_parlays = (
        _top_parlays_payload(merged, odds_source, min_edge, max_parlays)
        if has_odds
        else []
    )
    top_totals = _top_totals(slate) if totals_by_game else []

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
        "date": game_date.isoformat(),
        "mode": "demo" if use_cache else "live",
        "disclaimer": DISCLAIMER,
        "warnings": warnings,
        "odds_source": odds_source,
        "games_on_slate": len(slate_df),
        "games_with_odds": int(merged["home_ml"].notna().sum()) if has_odds else 0,
        "slate": slate,
        "top_singles": top_singles,
        "top_parlays": top_parlays,
        "top_totals": top_totals,
        "totals_disclaimer": "Totals O/U model is experimental and separate from moneyline v3.",
        "display_note": (
            "Win % uses 50% model + 50% market when odds available; "
            "raw model when odds missing."
        ),
        "edge_threshold": min_edge,
        "max_parlays": max_parlays,
        "skip_totals": skip_totals,
        "status": _status_footer(),
    }
    payload = _sanitize_json(payload)
    _write_cache(payload)
    return payload


def _write_cache(payload: dict[str, Any]) -> None:
    DAILY_BOARD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DAILY_BOARD_CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
