"""UFC fighter profile — career stats, Elo, weight-class history, next fight."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from app.features.ufc_pregame import build_fighter_tracker_from_history
from app.models.ufc_baseline import current_elo_ratings, load_fights
from app.odds.ufc_fighter_aliases import (
    fighter_match_key,
    fighter_slug,
    fighters_match,
    normalize_fighter_name,
)
from app.services.schedule_ufc import get_ufc_schedule
from app.services.ufc_fighter_media import enrich_fight_media, lookup_fighter_media


def _json_safe(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_json_safe(v) for v in val]
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    if isinstance(val, (str, int, bool)) or val is None:
        return val
    if pd.isna(val):
        return None
    return val


def _last5_record_label(win_pct: float | None) -> str:
    if win_pct is None:
        return "—"
    wins = max(0, min(5, round(float(win_pct) * 5)))
    return f"{wins}-{5 - wins}"


def _career_record(prior: list) -> str:
    if not prior:
        return "0-0"
    wins = sum(g.win for g in prior)
    return f"{wins}-{len(prior) - wins}"


def _last5_win_pct(prior: list) -> float | None:
    if not prior:
        return None
    recent = prior[-5:]
    return sum(g.win for g in recent) / len(recent)


def _collect_fighter_names() -> dict[str, str]:
    """slug -> canonical display name."""
    names: dict[str, str] = {}
    try:
        fights = load_fights()
        for row in fights.itertuples(index=False):
            for raw in (row.home_team, row.away_team):
                norm = normalize_fighter_name(str(raw))
                if norm:
                    names[fighter_slug(norm)] = norm
    except (FileNotFoundError, OSError):
        pass

    try:
        schedule = get_ufc_schedule(None, auto_resolve=True)
        for fight in schedule.get("games") or []:
            for raw in (
                fight.get("home_team"),
                fight.get("away_team"),
                fight.get("home_fighter"),
                fight.get("away_fighter"),
            ):
                norm = normalize_fighter_name(str(raw or ""))
                if norm:
                    names[fighter_slug(norm)] = norm
    except Exception:
        pass
    return names


def resolve_fighter_by_slug(slug: str) -> str | None:
    """Resolve URL slug to canonical fighter display name."""
    key = str(slug or "").strip().lower()
    if not key:
        return None
    return _collect_fighter_names().get(key)


def _weight_class_history(fights: pd.DataFrame, fighter_name: str) -> list[dict[str, Any]]:
    target = fighter_match_key(fighter_name)
    history: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in fights.sort_values(["date", "fight_id"]).itertuples(index=False):
        home_key = fighter_match_key(normalize_fighter_name(str(row.home_team)))
        away_key = fighter_match_key(normalize_fighter_name(str(row.away_team)))
        if target not in (home_key, away_key):
            continue
        wc = str(getattr(row, "weight_class", "") or "").strip()
        if not wc:
            continue
        label = wc.replace(" Bout", "").replace(" bout", "").strip()
        if label and label not in seen:
            seen.add(label)
            history.append(
                {
                    "weight_class": label,
                    "date": pd.to_datetime(row.date).date().isoformat(),
                    "event_name": str(getattr(row, "event_name", "") or ""),
                }
            )
    return history


def _recent_fights(
    fights: pd.DataFrame,
    fighter_name: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    target = fighter_match_key(fighter_name)
    rows: list[dict[str, Any]] = []
    completed = fights[fights["home_win"].notna()].sort_values(
        ["date", "fight_id"], ascending=[False, False]
    )
    for row in completed.itertuples(index=False):
        home = normalize_fighter_name(str(row.home_team))
        away = normalize_fighter_name(str(row.away_team))
        home_key = fighter_match_key(home)
        away_key = fighter_match_key(away)
        if target == home_key:
            side = "home"
            opponent = away
            won = int(row.home_win) == 1
        elif target == away_key:
            side = "away"
            opponent = home
            won = int(row.home_win) == 0
        else:
            continue
        rows.append(
            {
                "date": pd.to_datetime(row.date).date().isoformat(),
                "opponent": opponent,
                "opponent_slug": fighter_slug(opponent),
                "result": "W" if won else "L",
                "weight_class": str(getattr(row, "weight_class", "") or ""),
                "event_name": str(getattr(row, "event_name", "") or ""),
                "fight_id": str(row.fight_id),
                "side": side,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _find_next_fight(fighter_name: str) -> dict[str, Any] | None:
    try:
        schedule = get_ufc_schedule(None, auto_resolve=True)
    except Exception:
        return None
    fights = schedule.get("games") or []
    if not fights:
        return None
    resolved = schedule.get("resolved_date") or schedule.get("date")
    if not resolved:
        return None
    slate_day = date.fromisoformat(str(resolved)[:10])

    for fight in fights:
        home = normalize_fighter_name(fight.get("home_team") or fight.get("home_fighter") or "")
        away = normalize_fighter_name(fight.get("away_team") or fight.get("away_fighter") or "")
        if not (fighters_match(fighter_name, home) or fighters_match(fighter_name, away)):
            continue
        enriched = enrich_fight_media(dict(fight), slate_day)
        fid = str(enriched.get("fight_id") or enriched.get("game_id") or "")
        corner = "home" if fighters_match(fighter_name, home) else "away"
        opponent = away if corner == "home" else home
        return {
            "fight_id": fid,
            "card_date": slate_day.isoformat(),
            "event_name": enriched.get("event_name"),
            "weight_class": enriched.get("weight_class"),
            "card_segment": enriched.get("card_segment"),
            "opponent": opponent,
            "opponent_slug": fighter_slug(opponent),
            "corner": corner,
            "matchup": f"{away} vs {home}",
            "href": f"/ufc/game/{fid}?date={slate_day.isoformat()}",
            "start_time_utc": enriched.get("start_time_utc"),
        }
    return None


def get_ufc_fighter_profile(slug: str) -> dict[str, Any] | None:
    """Build fighter profile payload for /api/ufc/fighter/{slug}."""
    fighter_name = resolve_fighter_by_slug(slug)
    if not fighter_name:
        return None

    today = date.today()
    as_of = pd.Timestamp(today)

    try:
        fights = load_fights()
    except (FileNotFoundError, OSError):
        fights = pd.DataFrame()

    completed = (
        fights[fights["home_win"].notna()].copy() if not fights.empty else pd.DataFrame()
    )
    if not completed.empty:
        completed["date"] = pd.to_datetime(completed["date"])
        completed["home_team"] = completed["home_team"].map(normalize_fighter_name)
        completed["away_team"] = completed["away_team"].map(normalize_fighter_name)

    hist_before = completed[completed["date"] < as_of] if not completed.empty else completed
    tracker = build_fighter_tracker_from_history(hist_before)
    prior = tracker.fights_before(fighter_name, as_of)

    elo_rating = None
    if not hist_before.empty:
        ratings = current_elo_ratings(hist_before)
        elo_rating = round(ratings.get(fighter_name, 1500.0), 1)

    last5_pct = _last5_win_pct(prior)
    next_fight = _find_next_fight(fighter_name)
    media_date = None
    if next_fight and next_fight.get("card_date"):
        media_date = date.fromisoformat(str(next_fight["card_date"])[:10])
    media = lookup_fighter_media(fighter_name, prefer_date=media_date)

    weight_history = _weight_class_history(completed, fighter_name) if not completed.empty else []
    recent = _recent_fights(completed, fighter_name) if not completed.empty else []

    return _json_safe(
        {
            "slug": fighter_slug(fighter_name),
            "name": fighter_name,
            "sport": "ufc",
            "career_record": _career_record(prior),
            "last5_record": _last5_record_label(last5_pct),
            "last5_win_pct": round(last5_pct, 4) if last5_pct is not None else None,
            "elo_rating": elo_rating,
            "weight_class_history": weight_history,
            "current_weight_class": weight_history[-1]["weight_class"] if weight_history else None,
            "recent_fights": recent,
            "next_fight": next_fight,
            "portrait": {
                "headshot_url": media.get("headshot_url"),
                "flag_url": media.get("flag_url"),
                "flag_backdrop_url": media.get("flag_backdrop_url"),
                "country": media.get("country"),
                "country_code": media.get("country_code"),
                "athlete_id": media.get("athlete_id"),
            },
            "href": f"/ufc/fighter/{fighter_slug(fighter_name)}",
        }
    )
