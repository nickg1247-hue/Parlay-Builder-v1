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
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_math import market_probs_from_american
from app.odds.spread_math import market_probs_from_american_spread
from app.odds.team_aliases import is_valid_american_odds
from app.models.calibration import (
    blend_display_prob,
    model_disagrees_heavy_favorite,
)
from app.models.constants import DEFAULT_MIN_EDGE
from app.models.mlb_baseline import load_games
from app.models.production_pipeline import get_active_model_info
from app.models.mlb_ensemble import model_pick_from_prob
from app.services.mlb_data_freshness import check_mlb_prediction_freshness
from app.services.forward_clv import log_live_picks
from app.parlay.ev_ranker import (
    DEFAULT_MAX_PARLAYS,
    attach_market_odds,
    rank_parlays,
    _candidate_legs,
)
from app.parlay.slate import (
    build_slate_dataframe,
    build_slate_from_history,
    fetch_mlb_schedule_day,
    filter_board_games,
    slate_filter_meta,
)
from app.parlay.totals_slate import build_totals_slate

logger = logging.getLogger(__name__)

DAILY_BOARD_CACHE = PROJECT_ROOT / "data" / "processed" / "daily_board.json"
BOARD_CACHE_TTL_SECONDS = 300
MORNING_BOARD_MAX_AGE_SECONDS = 24 * 3600
DISCLAIMER = (
    "Experimental analytics — not betting advice. EV signals are not validated. "
    "Totals model is separate from moneyline."
)
CONFIDENCE_DISCLAIMER = (
    "Confidence reflects model vs market edge, not a guarantee."
)
SPREAD_DISCLAIMER = (
    "Run line model is experimental (margin GBR + Normal cover); not validated like moneyline v3."
)


def _spread_row_fields(row, min_edge: float) -> dict[str, Any]:
    """Compute spread cover probs, edges, and optional +EV pick for one slate row."""
    empty: dict[str, Any] = {
        "home_spread_point": None,
        "home_spread_american": None,
        "away_spread_point": None,
        "away_spread_american": None,
        "model_prob_home_cover": None,
        "model_prob_away_cover": None,
        "market_prob_home_cover": None,
        "market_prob_away_cover": None,
        "spread_edge_home": None,
        "spread_edge_away": None,
        "plus_ev_spread": False,
        "spread_best_pick": None,
    }
    hp = getattr(row, "home_spread_point", None)
    hap = getattr(row, "home_spread_american", None)
    ap = getattr(row, "away_spread_point", None)
    aap = getattr(row, "away_spread_american", None)
    if hp is None or ap is None or pd.isna(hp) or pd.isna(ap):
        return empty
    if not is_valid_american_odds(hap) or not is_valid_american_odds(aap):
        return {
            **empty,
            "home_spread_point": float(hp),
            "away_spread_point": float(ap),
        }

    market_home, market_away = market_probs_from_american_spread(
        float(hp), int(hap), float(ap), int(aap)
    )
    model_home = getattr(row, "model_prob_home_cover", None)
    model_away = getattr(row, "model_prob_away_cover", None)
    edge_home = None
    edge_away = None
    if model_home is not None and not (isinstance(model_home, float) and math.isnan(model_home)):
        edge_home = float(model_home) - market_home
    if model_away is not None and not (isinstance(model_away, float) and math.isnan(model_away)):
        edge_away = float(model_away) - market_away

    spread_best = None
    plus_ev = False
    options: list[tuple[str, str, float, int, float]] = []
    if edge_home is not None:
        options.append(
            ("home", row.home_team, edge_home, int(hap), float(hp))
        )
    if edge_away is not None:
        options.append(
            ("away", row.away_team, edge_away, int(aap), float(ap))
        )
    if options:
        side, team, edge, am, point = max(options, key=lambda x: x[2])
        if edge >= min_edge:
            plus_ev = True
            spread_best = {
                "side": side,
                "team": team,
                "edge": round(edge, 4),
                "american_odds": am,
                "spread_point": point,
            }

    return {
        "home_spread_point": float(hp),
        "home_spread_american": int(hap),
        "away_spread_point": float(ap),
        "away_spread_american": int(aap),
        "model_prob_home_cover": model_home,
        "model_prob_away_cover": model_away,
        "market_prob_home_cover": round(market_home, 4),
        "market_prob_away_cover": round(market_away, 4),
        "spread_edge_home": round(edge_home, 4) if edge_home is not None else None,
        "spread_edge_away": round(edge_away, 4) if edge_away is not None else None,
        "plus_ev_spread": plus_ev,
        "spread_best_pick": spread_best,
    }


def confidence_label(edge: float | None) -> str:
    """Map absolute model-vs-market edge to a display confidence tier."""
    if edge is None:
        return "—"
    abs_edge = abs(float(edge))
    if abs_edge < 0.04:
        return "Low"
    if abs_edge < 0.08:
        return "Medium"
    if abs_edge < 0.12:
        return "High"
    return "Extremely high"


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


def _history_stale_warning(game_date: date, use_cache: bool) -> str | None:
    """Warn when ingested history is too old for reliable live feature scoring."""
    if use_cache:
        return None
    try:
        hist = load_games()
    except Exception:
        return "Game history unavailable — run ingest before live board."
    if hist.empty:
        return "Game history empty — run ingest before live board."
    max_date = pd.to_datetime(hist["date"]).max()
    gap_days = (pd.Timestamp(game_date) - max_date).days
    if gap_days > 7:
        return (
            f"Game history last updated {max_date.date().isoformat()} "
            f"({gap_days} days before board date). Re-run ingest for current-season "
            f"stats, rest days, and pitcher rates."
        )
    return None


def _build_slate(
    game_date: date,
    use_cache: bool,
    api_games: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    if use_cache:
        slate = build_slate_from_history(game_date)
        if not slate.empty:
            return slate
    return build_slate_dataframe(game_date, api_games=api_games)


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
    *,
    block_strong_picks: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        matchup = f"{row.away_team} @ {row.home_team}"
        model_home = float(row.model_prob_home)
        model_home_raw = float(getattr(row, "model_prob_home_raw", model_home))
        pick_reconciled = bool(getattr(row, "pick_reconciled", False))
        market_home = None
        edge_home = None
        ml_edge_best = None
        ml_confidence = confidence_label(None)
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
                ml_edge_best = max(edge_home, edge_away)
                ml_confidence = confidence_label(ml_edge_best)
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

        pick = model_pick_from_prob(
            model_home,
            row.home_team,
            row.away_team,
            block_strong_picks=block_strong_picks,
        )
        model_pick_side = pick.model_pick_side
        model_pick_team = pick.model_pick_team
        model_pick_prob = pick.model_pick_prob

        totals = totals_by_game.get(str(row.game_id), {})
        ou_line = totals.get("ou_line")
        if ou_line is not None and pd.isna(ou_line):
            ou_line = None
        exp_runs = totals.get("expected_total_runs")
        total_edge = totals.get("total_edge")
        if total_edge is not None and pd.isna(total_edge):
            total_edge = None
        totals_confidence = confidence_label(
            abs(float(total_edge)) if total_edge is not None else None
        )
        spread = _spread_row_fields(row, min_edge)
        model_cover_side = None
        model_cover_team = None
        mhc = spread.get("model_prob_home_cover")
        mac = spread.get("model_prob_away_cover")
        if mhc is not None and mac is not None:
            if float(mhc) >= float(mac):
                model_cover_side = "home"
                model_cover_team = row.home_team
            else:
                model_cover_side = "away"
                model_cover_team = row.away_team
        rows.append(
            {
                "game_id": str(row.game_id),
                "matchup": matchup,
                "away_team": row.away_team,
                "home_team": row.home_team,
                "model_prob_home": round(model_home, 4),
                "model_prob_home_raw": round(model_home_raw, 4),
                "display_prob_home": round(display_home, 4),
                "market_prob_home": round(market_home, 4) if market_home else None,
                "edge_home": round(edge_home, 4) if edge_home is not None else None,
                "ml_edge_best": (
                    round(ml_edge_best, 4) if ml_edge_best is not None else None
                ),
                "ml_confidence": ml_confidence,
                "plus_ev_single": plus_ev,
                "best_pick": best_pick,
                "model_pick_side": model_pick_side,
                "model_pick_team": model_pick_team,
                "model_pick_prob": round(model_pick_prob, 4) if model_pick_prob is not None else None,
                "model_pick_action": pick.model_pick_action,
                "model_confidence": pick.model_confidence,
                "model_confidence_prob": pick.model_confidence_prob,
                "ev_pick_side": best_pick["side"] if best_pick else None,
                "ev_pick_team": best_pick["team"] if best_pick else None,
                "ev_pick_edge": best_pick["edge"] if best_pick else None,
                "ml_picks_disagree": bool(
                    best_pick
                    and model_pick_side
                    and best_pick["side"] != model_pick_side
                ),
                "model_disagrees_heavy_favorite": disagree,
                "pick_reconciled": pick_reconciled,
                "ou_line": ou_line,
                "expected_total_runs": (
                    round(float(exp_runs), 2) if exp_runs is not None else None
                ),
                "totals_pick": totals.get("pick"),
                "model_prob_over": totals.get("model_prob_over"),
                "market_prob_over": totals.get("market_prob_over"),
                "total_edge": total_edge,
                "totals_confidence": totals_confidence,
                "plus_ev_total": totals.get("plus_ev_total", False),
                "model_cover_side": model_cover_side,
                "model_cover_team": model_cover_team,
                **_pitcher_fields(row),
                **spread,
            }
        )
    return rows


def _totals_by_game(
    game_date: date,
    use_cache: bool,
    slate_df: pd.DataFrame,
    attach_market_odds: bool = True,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    try:
        totals_df = build_totals_slate(
            game_date,
            use_cache=use_cache,
            moneyline_slate=slate_df,
            attach_market_odds=attach_market_odds,
            force_refresh=force_refresh,
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


def _pitcher_fields(row: object) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "home_starting_pitcher",
        "away_starting_pitcher",
        "home_pitcher_era",
        "away_pitcher_era",
    ):
        val = getattr(row, key, None)
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            if isinstance(val, str):
                out[key] = val.strip() or None
            else:
                out[key] = round(float(val), 2) if key.endswith("_era") else val
    return out


def _top_singles(
    slate: list[dict[str, Any]],
    game_date: date,
    limit: int = 5,
) -> list[dict[str, Any]]:
    picks = []
    for game in slate:
        if game.get("best_pick"):
            picks.append(
                {
                    "game_id": game.get("game_id"),
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
    from app.services.bet_context import enrich_ml_singles

    return enrich_ml_singles(picks[:limit], slate, game_date)


def _top_form_singles(
    slate: list[dict[str, Any]],
    game_date: date,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Top ML picks for the home page ranked by team form (L5 / L10 / season), not +EV."""
    from app.services.bet_context import enrich_ml_singles, form_composite_score

    picks: list[dict[str, Any]] = []
    for game in slate:
        side = game.get("model_pick_side")
        team = game.get("model_pick_team")
        best = game.get("best_pick")
        if not side or not team:
            if not best:
                continue
            side = best["side"]
            team = best["team"]

        model_prob = game.get("model_pick_prob")
        if model_prob is None and game.get("model_prob_home") is not None:
            model_prob = (
                float(game["model_prob_home"])
                if side == "home"
                else round(1 - float(game["model_prob_home"]), 4)
            )

        if side == "home":
            american = game.get("home_ml")
        else:
            american = game.get("away_ml")
        if best and best.get("team") == team:
            american = best.get("american_odds") or american
            edge = best.get("edge")
        else:
            edge = game.get("ev_pick_edge") if game.get("ev_pick_team") == team else None

        picks.append(
            {
                "game_id": game.get("game_id"),
                "matchup": game.get("matchup"),
                "team": team,
                "side": side,
                "edge": edge,
                "american_odds": american,
                "model_prob": model_prob,
            }
        )

    enriched = enrich_ml_singles(picks, slate, game_date)
    enriched.sort(key=form_composite_score, reverse=True)
    return enriched[:limit]


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


def _board_age_seconds(cached: dict[str, Any]) -> float:
    generated = datetime.fromisoformat(
        cached["generated_at"].replace("Z", "+00:00")
    )
    return (datetime.now(timezone.utc) - generated).total_seconds()


def _try_load_cached_board(
    game_date: date,
    cache_key: str,
    refresh: bool,
    use_cache: bool,
    min_edge: float,
    max_parlays: int,
) -> dict[str, Any] | None:
    """Exact 5-min TTL match, or morning board fallback (24h, skip_totals=False on disk)."""
    if refresh or not DAILY_BOARD_CACHE.exists():
        return None
    try:
        cached = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read daily board cache: %s", exc)
        return None

    if cached.get("cache_key") == cache_key:
        if _board_age_seconds(cached) < BOARD_CACHE_TTL_SECONDS:
            return cached

    if use_cache:
        return None
    if cached.get("date") != game_date.isoformat():
        return None
    if cached.get("skip_totals") is not False:
        return None
    suffix = f"_edge{min_edge}_parlays{max_parlays}"
    if not str(cached.get("cache_key", "")).endswith(suffix):
        return None
    if _board_age_seconds(cached) < MORNING_BOARD_MAX_AGE_SECONDS:
        logger.info(
            "Serving morning daily board for %s (skip_totals/cache_key fallback)",
            game_date.isoformat(),
        )
        return cached
    return None


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
    odds_force_refresh: bool | None = None,
    live_test: bool = False,
) -> dict[str, Any]:
    game_date = game_date or date.today()
    # Board "Run live" bypass: force API + full totals board for main-site game pages.
    if live_test and not use_cache:
        refresh = True
        skip_totals = False
        odds_force_refresh = True
    # Live board defaults to fast path; pass skip_totals=false to include O/U model.
    elif skip_totals is None:
        skip_totals = not use_cache
    cache_key = (
        f"{game_date.isoformat()}_{'cache' if use_cache else 'live'}"
        f"_{'no_totals' if skip_totals else 'totals'}"
        f"_edge{min_edge}_parlays{max_parlays}"
    )

    cached_board = _try_load_cached_board(
        game_date, cache_key, refresh, use_cache, min_edge, max_parlays
    )
    if cached_board is not None:
        return cached_board

    force_odds = refresh if odds_force_refresh is None else odds_force_refresh

    warnings: list[str] = []
    stale = _history_stale_warning(game_date, use_cache)
    if stale:
        warnings.append(stale)
    freshness = check_mlb_prediction_freshness(game_date, use_cache=use_cache)
    warnings.extend(freshness.get("issues", []))
    block_strong_picks = bool(freshness.get("block_strong_picks"))
    if not use_cache and not live_odds_enabled():
        if not _has_odds_api_key():
            warnings.append(
                "Free mode: no live sportsbook lines (ODDS_API_KEY not set). "
                "Showing model picks only. See DEV.md to enable USE_LIVE_ODDS."
            )
        else:
            warnings.append(
                "Free mode: USE_LIVE_ODDS=false — model picks only, no sportsbook API calls."
            )

    slate_filter_counts = {"final": 0, "postponed": 0, "date_mismatch": 0, "game_type": 0}
    if use_cache:
        slate_df = _build_slate(game_date, use_cache)
    else:
        raw_api_games = fetch_mlb_schedule_day(game_date)
        slate_filter_counts = slate_filter_meta(raw_api_games, game_date)
        filtered_games = filter_board_games(raw_api_games, game_date)
        slate_df = _build_slate(game_date, use_cache, api_games=filtered_games)

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
            "slate_filter_meta": slate_filter_counts,
            "active_moneyline_model": get_active_model_info("moneyline"),
            "active_totals_model": get_active_model_info("totals"),
            "status": _status_footer(),
        }
        payload = _sanitize_json(payload)
        _write_cache(payload)
        return payload

    merged, odds_source = attach_market_odds(
        slate_df,
        game_date,
        use_cache=use_cache,
        force_refresh=force_odds,
        bypass_min_ttl=bool(live_test and not use_cache),
    )
    from app.odds.odds_repository import last_fetch_meta

    meta = last_fetch_meta()
    if meta.get("quota_warning"):
        warnings.append(meta["quota_warning"])
    if odds_source == "none" and not use_cache and not live_odds_enabled():
        odds_source = "model_only"
    elif odds_source == "none":
        warnings.append(
            "Odds unavailable — slate shows model probabilities only. "
            "Use ?use_cache=true for historical demo or enable USE_LIVE_ODDS in .env."
        )

    has_odds = odds_source not in ("none", "model_only")
    # Always score model runs; attach sportsbook O/U when skip_totals is false.
    totals_by_game = _totals_by_game(
        game_date,
        use_cache,
        slate_df,
        attach_market_odds=not skip_totals,
        force_refresh=force_odds,
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

    try:
        from app.models.mlb_spread import predict_spread_covers

        merged = predict_spread_covers(merged)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Spread cover predictions skipped: %s", exc)

    from app.services.mlb_pick_reconcile import reconcile_slate_dataframe

    market_by_game: dict[str, float] | None = None
    if has_odds and "home_ml" in merged.columns:
        from app.odds.odds_math import market_probs_from_american
        from app.odds.team_aliases import is_valid_american_odds

        market_by_game = {}
        for row in merged.itertuples(index=False):
            if is_valid_american_odds(getattr(row, "home_ml", None)) and is_valid_american_odds(
                getattr(row, "away_ml", None)
            ):
                mh, _ = market_probs_from_american(int(row.home_ml), int(row.away_ml))
                market_by_game[str(row.game_id)] = mh
    merged = reconcile_slate_dataframe(merged, market_probs_home=market_by_game)

    slate = _slate_rows(
        merged, has_odds, totals_by_game, min_edge, block_strong_picks=block_strong_picks
    )
    top_singles = _top_singles(slate, game_date) if has_odds else []
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
        "slate_filter_meta": slate_filter_counts,
        "totals_disclaimer": "Totals O/U model is experimental and separate from moneyline v3.",
        "spread_disclaimer": SPREAD_DISCLAIMER,
        "display_note": (
            "Win % uses 50% model + 50% market when odds available; "
            "raw model when odds missing."
        ),
        "confidence_disclaimer": CONFIDENCE_DISCLAIMER,
        "edge_threshold": min_edge,
        "max_parlays": max_parlays,
        "skip_totals": skip_totals,
        "prediction_freshness": freshness,
        "active_moneyline_model": get_active_model_info("moneyline"),
        "active_totals_model": get_active_model_info("totals"),
        "status": _status_footer(),
    }
    if live_test and not use_cache:
        repo_meta = last_fetch_meta()
        payload["board_live_test"] = True
        payload["synced_to_main"] = True
        payload["repository_fetched_at"] = repo_meta.get("fetched_at")
    payload = _sanitize_json(payload)
    if (
        not use_cache
        and payload.get("mode") == "live"
        and payload.get("odds_source") == "the_odds_api"
        and live_odds_enabled()
    ):
        try:
            log_live_picks(payload)
        except Exception as exc:
            logger.warning("Forward CLV log skipped: %s", exc)
    _write_cache(payload)
    return payload


def _write_cache(payload: dict[str, Any]) -> None:
    DAILY_BOARD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DAILY_BOARD_CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
