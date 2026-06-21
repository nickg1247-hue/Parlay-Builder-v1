"""Log offered MLB player props and grade strong vs moderate line accuracy."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.prop_scoring import player_stat_on_date

PROP_PICK_LOG = PROJECT_ROOT / "data" / "processed" / "prop_pick_log.jsonl"
ODDS_MOVE_THRESHOLD = 5
STRENGTH_BUCKETS = ("strong", "moderate", "weak")


def prop_pick_id(
    board_date: str,
    game_id: str,
    player: str,
    market_type: str,
    line: float,
    side: str,
    bookmaker: str,
) -> str:
    line_key = f"{float(line):g}"
    player_key = str(player or "").strip().lower()
    return (
        f"{board_date}:{game_id}:{player_key}:{market_type}:"
        f"{line_key}:{side}:{bookmaker}"
    )


def _track_all_actionable() -> bool:
    return os.getenv("PROP_TRACK_ALL_ACTIONABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _should_log_prop(prop: dict[str, Any]) -> bool:
    if not prop.get("actionable"):
        return False
    if prop.get("recommended_odds") is None:
        return False
    if prop.get("recommended_side") is None:
        return False
    strength = str(prop.get("line_strength") or "weak")
    if _track_all_actionable():
        return True
    return strength in ("strong", "moderate")


def _read_all_rows() -> list[dict[str, Any]]:
    if not PROP_PICK_LOG.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in PROP_PICK_LOG.read_text(encoding="utf-8").splitlines():
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
    PROP_PICK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PROP_PICK_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def grade_prop_result(
    actual_stat: float,
    line: float,
    side: str,
) -> tuple[bool | None, str]:
    """Return (hit, result_status). hit is None on a push."""
    if side == "over":
        if actual_stat > line:
            return True, "settled"
        if actual_stat < line:
            return False, "settled"
        return None, "push"
    if actual_stat < line:
        return True, "settled"
    if actual_stat > line:
        return False, "settled"
    return None, "push"


def log_offered_props(
    props: list[dict[str, Any]],
    board_date: str,
    *,
    source: str = "daily_props",
) -> list[dict[str, Any]]:
    """
    Append actionable strong/moderate props to the forward tracker log.
    Skips duplicate pick_id unless American odds moved by >= 5.
    """
    logged_at = datetime.now(timezone.utc).isoformat()
    existing = _latest_by_pick_id(_read_all_rows())
    written: list[dict[str, Any]] = []

    for prop in props:
        if not _should_log_prop(prop):
            continue
        game_id = str(prop.get("game_id") or "")
        player = str(prop.get("player") or "")
        market_type = str(prop.get("market_type") or "")
        side = str(prop.get("recommended_side") or "")
        bookmaker = str(prop.get("bookmaker") or "consensus")
        line = float(prop.get("line") or 0)
        if not game_id or not player or not market_type or not side:
            continue

        pid = prop_pick_id(board_date, game_id, player, market_type, line, side, bookmaker)
        american = prop.get("recommended_odds")
        prior = existing.get(pid)
        if prior is not None and american is not None:
            prior_odds = prior.get("american_odds_at_offer")
            if prior_odds is not None and abs(int(american) - int(prior_odds)) < ODDS_MOVE_THRESHOLD:
                continue

        row = {
            "pick_id": pid,
            "logged_at": logged_at,
            "board_date": board_date,
            "game_id": game_id,
            "matchup": prop.get("matchup"),
            "player": player,
            "market_type": market_type,
            "market_label": prop.get("market_label"),
            "line": line,
            "recommended_side": side,
            "american_odds_at_offer": american,
            "recommended_hit_rate": prop.get("recommended_hit_rate"),
            "score": prop.get("score"),
            "line_strength": prop.get("line_strength"),
            "line_strength_label": prop.get("line_strength_label"),
            "line_insight": prop.get("line_insight"),
            "bookmaker": bookmaker,
            "source": source,
            "actual_stat": None,
            "hit": None,
            "result_status": "pending",
            "settled_at": None,
        }
        _append_row(row)
        existing[pid] = row
        written.append(row)
    return written


def backfill_prop_results(game_date: date | None = None) -> dict[str, Any]:
    """Fill actual stats and hit/miss for pending offered props."""
    rows = _read_all_rows()
    if not rows:
        return {"updated": 0, "pending": 0, "dnp": 0}

    today = datetime.now(timezone.utc).date()
    latest = _latest_by_pick_id(rows)
    updated = 0
    dnp = 0
    settled_at = datetime.now(timezone.utc).isoformat()

    for pid, row in latest.items():
        if row.get("result_status") not in (None, "pending"):
            continue
        board_date_str = row.get("board_date")
        if not board_date_str:
            continue
        if game_date is not None and board_date_str != game_date.isoformat():
            continue

        try:
            board_day = date.fromisoformat(board_date_str)
        except ValueError:
            continue
        if board_day > today:
            continue

        player = str(row.get("player") or "")
        market_type = str(row.get("market_type") or "")
        side = str(row.get("recommended_side") or "")
        line = float(row.get("line") or 0)
        season = board_day.year

        actual = player_stat_on_date(player, market_type, season, board_date_str)
        if actual is None:
            if board_day < today:
                new_row = {
                    **row,
                    "result_status": "dnp",
                    "settled_at": settled_at,
                }
                _append_row(new_row)
                dnp += 1
            continue

        hit, status = grade_prop_result(float(actual), line, side)
        new_row = {
            **row,
            "actual_stat": round(float(actual), 3),
            "hit": hit,
            "result_status": status,
            "settled_at": settled_at,
        }
        _append_row(new_row)
        updated += 1

    pending = sum(
        1
        for r in _latest_by_pick_id(_read_all_rows()).values()
        if r.get("result_status") == "pending"
    )
    return {"updated": updated, "pending": pending, "dnp": dnp}


def _bucket_stats() -> dict[str, dict[str, Any]]:
    return {
        strength: {
            "offered": 0,
            "settled": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "hit_rate": None,
        }
        for strength in STRENGTH_BUCKETS
    }


def summarize_prop_tracker(days: int = 30) -> dict[str, Any]:
    """Aggregate offered prop accuracy by line strength."""
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

    buckets = _bucket_stats()
    status_counts: dict[str, int] = {}

    for row in rows:
        strength = str(row.get("line_strength") or "weak")
        if strength not in buckets:
            strength = "weak"
        buckets[strength]["offered"] += 1

        status = row.get("result_status") or "pending"
        status_counts[status] = status_counts.get(status, 0) + 1

        if status != "settled":
            continue
        buckets[strength]["settled"] += 1
        hit = row.get("hit")
        if hit is True:
            buckets[strength]["hits"] += 1
        elif hit is False:
            buckets[strength]["misses"] += 1
        else:
            buckets[strength]["pushes"] += 1

    for bucket in buckets.values():
        decided = bucket["hits"] + bucket["misses"]
        if decided:
            bucket["hit_rate"] = round(bucket["hits"] / decided, 4)

    total_settled = sum(b["settled"] for b in buckets.values())
    total_hits = sum(b["hits"] for b in buckets.values())
    total_misses = sum(b["misses"] for b in buckets.values())
    overall_hit_rate = (
        round(total_hits / (total_hits + total_misses), 4)
        if (total_hits + total_misses)
        else None
    )

    return {
        "days": days,
        "props_logged": len(rows),
        "props_settled": total_settled,
        "overall_hit_rate": overall_hit_rate,
        "line_strength": buckets,
        "result_status_counts": status_counts,
    }


def list_recent_picks(limit: int = 50) -> list[dict[str, Any]]:
    rows = list(_latest_by_pick_id(_read_all_rows()).values())
    rows.sort(key=lambda r: str(r.get("logged_at") or ""), reverse=True)
    return rows[: max(1, min(limit, 200))]
