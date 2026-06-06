"""Forward CLV logging for live daily board +EV moneyline singles."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.models.mlb_baseline import load_games
from app.odds.odds_math import american_to_decimal, market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.the_odds_api import fetch_mlb_moneylines
from app.parlay.ev_ranker import _parse_odds_api_events

# Append-only log; summarize/backfill use latest row per pick_id.
# If the same pick_id is logged again same day and American odds moved >= 5,
# append a new row (latest wins). Smaller moves are skipped (idempotent).
FORWARD_CLV_LOG = PROJECT_ROOT / "data" / "processed" / "forward_clv_log.jsonl"
ODDS_MOVE_THRESHOLD = 5


def pick_id(board_date: str, game_id: str, side: str) -> str:
    return f"{board_date}:{game_id}:{side}"


def clv_implied_prob(market_prob_at_pick: float, close_market_prob: float) -> float:
    """Positive when pick-side implied prob exceeds close (beat the close)."""
    return round(close_market_prob - market_prob_at_pick, 6)


def clv_decimal_ratio(pick_odds: int, close_odds: int) -> float:
    """> 0 when pick decimal odds exceed close (better price at pick time)."""
    return round(
        american_to_decimal(pick_odds) / american_to_decimal(close_odds) - 1.0,
        6,
    )


def _parse_events_with_commence(events: list[dict[str, Any]]) -> pd.DataFrame:
    """Median h2h lines plus full commence_time for close backfill."""
    base = _parse_odds_api_events(events)
    if base.empty or not events:
        return base
    commence_map: dict[tuple[str, str, str], str] = {}
    for event in events:
        home = normalize_team_name(event.get("home_team", ""))
        away = normalize_team_name(event.get("away_team", ""))
        day = (event.get("commence_time") or "")[:10]
        commence_map[(day, home, away)] = event.get("commence_time", "")
    base = base.copy()
    base["commence_time"] = base.apply(
        lambda r: commence_map.get((r["date"], r["home_team"], r["away_team"]), ""),
        axis=1,
    )
    return base


def _read_all_rows() -> list[dict[str, Any]]:
    if not FORWARD_CLV_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in FORWARD_CLV_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _latest_by_pick_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row.get("pick_id")
        if pid:
            out[pid] = row
    return out


def _append_row(row: dict[str, Any]) -> None:
    FORWARD_CLV_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FORWARD_CLV_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _picked_probs(game: dict[str, Any], side: str) -> tuple[float, float | None]:
    model_home = float(game["model_prob_home"])
    market_home = game.get("market_prob_home")
    if side == "home":
        model_prob = model_home
        market_prob = float(market_home) if market_home is not None else None
    else:
        model_prob = round(1.0 - model_home, 4)
        market_prob = round(1.0 - float(market_home), 4) if market_home is not None else None
    return model_prob, market_prob


def log_live_picks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Log +EV moneyline singles from a fresh live board payload.
    Caller must ensure mode=live and odds_source=the_odds_api.
    """
    if payload.get("mode") != "live":
        return []
    if payload.get("odds_source") != "the_odds_api":
        return []

    board_date = payload["date"]
    min_edge = float(payload.get("edge_threshold", 0.08))
    active_model = payload.get("active_moneyline_model") or {}
    model_version = active_model.get("model_version")
    logged_at = datetime.now(timezone.utc).isoformat()
    existing = _latest_by_pick_id(_read_all_rows())
    written: list[dict[str, Any]] = []

    for game in payload.get("slate") or []:
        if not game.get("plus_ev_single") or not game.get("best_pick"):
            continue
        pick = game["best_pick"]
        side = pick["side"]
        pid = pick_id(board_date, str(game["game_id"]), side)
        american = int(pick["american_odds"])
        prior = existing.get(pid)
        if prior is not None:
            prior_odds = prior.get("american_odds_at_pick")
            if prior_odds is not None and abs(american - int(prior_odds)) < ODDS_MOVE_THRESHOLD:
                continue

        model_prob, market_prob = _picked_probs(game, side)
        row = {
            "pick_id": pid,
            "logged_at": logged_at,
            "board_date": board_date,
            "game_id": str(game["game_id"]),
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "matchup": game["matchup"],
            "side": side,
            "team": pick["team"],
            "american_odds_at_pick": american,
            "model_prob": model_prob,
            "market_prob_at_pick": market_prob,
            "edge_at_pick": round(float(pick["edge"]), 4),
            "min_edge_threshold": min_edge,
            "model_version": model_version,
            "odds_source": payload.get("odds_source"),
            "close_american_odds": None,
            "close_market_prob": None,
            "close_fetched_at": None,
            "commence_time": None,
            "close_status": None,
            "clv_implied_prob": None,
            "clv_decimal_ratio": None,
            "home_win": None,
            "pick_won": None,
        }
        _append_row(row)
        existing[pid] = row
        written.append(row)
    return written


def _game_started(commence_time: str | None, now: datetime) -> bool:
    if not commence_time:
        return False
    try:
        commence = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        if commence.tzinfo is None:
            commence = commence.replace(tzinfo=timezone.utc)
        return now >= commence
    except ValueError:
        return False


def backfill_closing_odds(game_date: date | None = None) -> dict[str, Any]:
    """Fetch live odds and fill close fields on open log rows."""
    rows = _read_all_rows()
    if not rows:
        return {"updated": 0, "missed": 0, "pending": 0}

    events = fetch_mlb_moneylines()
    if not events:
        return {"updated": 0, "missed": 0, "pending": len(rows), "error": "no_odds_api"}

    odds_df = _parse_events_with_commence(events)
    if odds_df.empty:
        return {"updated": 0, "missed": 0, "pending": len(rows), "error": "no_parsed_odds"}

    odds_df["date_key"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    fetched_at = now.isoformat()
    latest = _latest_by_pick_id(rows)
    updated = 0
    missed = 0

    try:
        games = load_games()
        games["game_id"] = games["game_id"].astype(str)
        results_by_id = games.set_index("game_id")["home_win"].to_dict()
    except Exception:
        results_by_id = {}

    for pid, row in latest.items():
        if row.get("close_american_odds") is not None:
            continue
        if game_date is not None and row.get("board_date") != game_date.isoformat():
            continue

        match = odds_df[
            (odds_df["date_key"] == row["board_date"])
            & (odds_df["home_team"] == normalize_team_name(row["home_team"]))
            & (odds_df["away_team"] == normalize_team_name(row["away_team"]))
        ]
        commence = None
        if not match.empty:
            commence = match.iloc[0].get("commence_time") or None

        started = _game_started(commence, now)
        if match.empty:
            new_row = {**row, "close_status": "missed", "close_fetched_at": fetched_at}
            if commence:
                new_row["commence_time"] = commence
            _append_row(new_row)
            missed += 1
            continue

        m = match.iloc[0]
        home_ml = int(m["home_ml"])
        away_ml = int(m["away_ml"])
        if not is_valid_american_odds(home_ml) or not is_valid_american_odds(away_ml):
            new_row = {**row, "close_status": "missed", "close_fetched_at": fetched_at}
            _append_row(new_row)
            missed += 1
            continue

        market_home, market_away = market_probs_from_american(home_ml, away_ml)
        side = row["side"]
        close_odds = home_ml if side == "home" else away_ml
        close_prob = float(market_home) if side == "home" else float(market_away)
        pick_prob = row.get("market_prob_at_pick")
        clv_prob = None
        clv_dec = None
        if pick_prob is not None:
            clv_prob = clv_implied_prob(float(pick_prob), close_prob)
            clv_dec = clv_decimal_ratio(
                int(row["american_odds_at_pick"]), int(close_odds)
            )

        new_row = {
            **row,
            "close_american_odds": int(close_odds),
            "close_market_prob": round(close_prob, 4),
            "close_fetched_at": fetched_at,
            "commence_time": commence or m.get("commence_time"),
            "close_status": "missed" if started else "filled",
            "clv_implied_prob": clv_prob,
            "clv_decimal_ratio": clv_dec,
        }
        gid = str(row.get("game_id", ""))
        if gid in results_by_id and pd.notna(results_by_id[gid]):
            hw = int(results_by_id[gid])
            new_row["home_win"] = hw
            new_row["pick_won"] = bool(hw == 1 if side == "home" else hw == 0)
        _append_row(new_row)
        updated += 1
        if started:
            missed += 1

    pending = sum(
        1 for r in _latest_by_pick_id(_read_all_rows()).values() if r.get("close_american_odds") is None
    )
    return {"updated": updated, "missed": missed, "pending": pending}


def summarize_clv(days: int = 30) -> dict[str, Any]:
    """Aggregate forward CLV log for API report."""
    cutoff = datetime.now(timezone.utc).date()
    rows = list(_latest_by_pick_id(_read_all_rows()).values())
    if days > 0:
        min_date = cutoff - timedelta(days=days)
        rows = [
            r
            for r in rows
            if r.get("board_date")
            and date.fromisoformat(r["board_date"]) >= min_date
        ]

    with_close = [r for r in rows if r.get("close_american_odds") is not None]
    clv_vals = [
        float(r["clv_implied_prob"])
        for r in with_close
        if r.get("clv_implied_prob") is not None
    ]
    positive = sum(1 for v in clv_vals if v > 0)

    buckets = {
        "edge_8_12": {"count": 0, "positive_clv": 0, "clv_sum": 0.0},
        "edge_12_plus": {"count": 0, "positive_clv": 0, "clv_sum": 0.0},
    }
    for r in with_close:
        edge = float(r.get("edge_at_pick") or 0)
        clv = r.get("clv_implied_prob")
        if clv is None:
            continue
        key = "edge_12_plus" if edge >= 0.12 else "edge_8_12" if edge >= 0.08 else None
        if key is None:
            continue
        buckets[key]["count"] += 1
        buckets[key]["clv_sum"] += float(clv)
        if float(clv) > 0:
            buckets[key]["positive_clv"] += 1

    for b in buckets.values():
        if b["count"]:
            b["mean_clv_implied_prob"] = round(b["clv_sum"] / b["count"], 6)
            b["pct_positive_clv"] = round(b["positive_clv"] / b["count"], 4)
        else:
            b["mean_clv_implied_prob"] = None
            b["pct_positive_clv"] = None
        del b["clv_sum"]

    status_counts: dict[str, int] = {}
    for r in rows:
        st = r.get("close_status") or "pending"
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "days": days,
        "picks_logged": len(rows),
        "picks_with_close": len(with_close),
        "pct_positive_clv": round(positive / len(clv_vals), 4) if clv_vals else None,
        "mean_clv_implied_prob": round(sum(clv_vals) / len(clv_vals), 6) if clv_vals else None,
        "edge_buckets": buckets,
        "close_status_counts": status_counts,
    }
