"""MLB player props: Odds API lines + recent-form scoring."""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.odds_repository import (
    fetch_mlb_event_props_if_allowed,
    fetch_mlb_events_if_allowed,
)
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.the_odds_api import DEFAULT_MLB_PROP_MARKETS
from app.parlay.slate import fetch_mlb_schedule_day, filter_board_games
from app.services.prop_scoring import (
    _player_team_id,
    _search_player_id,
    market_label,
    score_prop,
    warm_scoring_cache,
)
from app.services.schedule_mlb import get_mlb_game, get_mlb_schedule

logger = logging.getLogger(__name__)

PROPS_DIR = PROJECT_ROOT / "data" / "processed" / "props_repository"
EVENTS_DIR = PROPS_DIR / "events"
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("PROPS_CACHE_TTL_SECONDS", "7200"))
MAX_PROP_LINES_TO_SCORE = int(os.getenv("MAX_PROP_LINES_TO_SCORE", "80"))


def _max_slate_prop_fetch(games_on_slate: int) -> int:
    """How many uncached games to fetch per scan. Default: entire slate (one call per day)."""
    override = os.getenv("PROP_SLATE_FETCH_MAX", "").strip()
    if override:
        return max(1, int(override))
    return max(1, games_on_slate)


def prop_side_hit_rates(prop: dict[str, Any]) -> tuple[float, float, float]:
    """L5, L10, and season hit rates for the recommended side."""
    side = prop.get("recommended_side") or "over"
    if side == "over":
        l5 = prop.get("hit_rate_over_l5")
        l10 = prop.get("hit_rate_over_l10") or prop.get("recommended_hit_rate")
        season = prop.get("hit_rate_over_season")
    else:
        l5 = prop.get("hit_rate_under_l5")
        l10 = prop.get("hit_rate_under_l10") or prop.get("recommended_hit_rate")
        season = prop.get("hit_rate_under_season")
    return (float(l5 or 0), float(l10 or 0), float(season or 0))


def prop_rank_key(prop: dict[str, Any]) -> tuple[float, float, float]:
    """Sort actionable props: highest L10, then L5, then season hit rate."""
    l5, l10, season = prop_side_hit_rates(prop)
    return (-l10, -l5, -season)


def prop_slip_leg(prop: dict[str, Any], *, game_id: str, matchup: str | None) -> dict[str, Any]:
    """Normalize a ranked prop row for the client bet slip."""
    side = prop.get("recommended_side") or "over"
    return {
        "id": "|".join(
            [
                str(game_id),
                str(prop.get("player", "")),
                str(prop.get("market_type", "")),
                str(prop.get("line", "")),
                side,
            ]
        ),
        "game_id": str(game_id),
        "matchup": matchup,
        "player": prop.get("player"),
        "market_type": prop.get("market_type"),
        "market_label": prop.get("market_label"),
        "side": side,
        "line": prop.get("line"),
        "american_odds": prop.get("recommended_odds"),
        "hit_rate": prop.get("recommended_hit_rate"),
        "score": prop.get("score"),
    }


def prop_is_bettable(prop: dict[str, Any], *, allow_stale: bool = False) -> bool:
    """
    True only when the recommended side has a live book price on that side.

    Blocks model-only picks, missing odds, wrong-side traps, and expired cache.
    """
    if not prop.get("actionable"):
        return False
    side = str(prop.get("recommended_side") or "").lower()
    if side not in ("over", "under"):
        return False
    recommended = prop.get("recommended_odds")
    if recommended is None:
        return False
    try:
        recommended_int = int(recommended)
    except (TypeError, ValueError):
        return False
    if not is_valid_american_odds(recommended_int):
        return False
    side_key = "over_odds" if side == "over" else "under_odds"
    listed = prop.get(side_key)
    if listed is None:
        return False
    try:
        listed_int = int(listed)
    except (TypeError, ValueError):
        return False
    if not is_valid_american_odds(listed_int):
        return False
    if prop.get("stale_cache") and not allow_stale:
        return False
    return True


def get_props_refresh_meta(game_date: date | None = None) -> dict[str, Any]:
    """Latest props slate cache metadata for refresh status UI."""
    game_date = game_date or date.today()
    cached = _load_json(_slate_cache_path(game_date))
    if not cached:
        return {"cached_at": None, "games_scanned": 0, "total_actionable": 0}
    return {
        "cached_at": cached.get("cached_at"),
        "games_scanned": cached.get("games_scanned", 0),
        "total_actionable": len(cached.get("all_props") or []),
    }


def _mark_stale_props(payload: dict[str, Any]) -> dict[str, Any]:
    """Downgrade actionable flags when serving expired prop cache."""
    if not payload.get("stale_cache"):
        return payload
    props: list[dict[str, Any]] = []
    for row in payload.get("props") or []:
        item = dict(row)
        item["stale_cache"] = True
        if item.get("actionable"):
            item["actionable"] = False
            item["actionable_reason"] = (
                "Cached lines expired — refresh props for current book prices"
            )
        props.append(item)
    top = [p for p in props if p.get("actionable") and p.get("score", 0) >= 60][:12]
    return {**payload, "props": props, "top_picks": top}


def _collect_actionable_props(payload: dict[str, Any], game_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    matchup = payload.get("matchup")
    stale = bool(payload.get("stale_cache"))
    for prop in payload.get("props") or []:
        row = dict(prop)
        if stale:
            row["stale_cache"] = True
        if not prop_is_bettable(row):
            continue
        if row.get("recommended_hit_rate") is None:
            continue
        rows.append(
            {
                **row,
                "game_id": game_id,
                "matchup": matchup,
                "slip_leg": prop_slip_leg(row, game_id=game_id, matchup=matchup),
            }
        )
    return rows


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _cache_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (_clock() - mtime).total_seconds()


def _props_cache_path(game_id: str) -> Path:
    return PROPS_DIR / f"{game_id}.json"


def _events_cache_path(game_date: date) -> Path:
    return EVENTS_DIR / f"{game_date.isoformat()}.json"


def _slate_cache_path(game_date: date) -> Path:
    return PROPS_DIR / f"slate_{game_date.isoformat()}.json"


def _median_int(values: list[int]) -> int | None:
    clean = [v for v in values if is_valid_american_odds(v)]
    if not clean:
        return None
    return int(round(statistics.median(clean)))


def _median_float(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _parse_event_props(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize Odds API event-odds response to prop rows."""
    grouped: dict[tuple[str, str, float, str], list[int]] = {}
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or "")
            if not market_key.startswith(("batter_", "pitcher_")):
                continue
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "").strip().lower()
                if name not in ("over", "under"):
                    continue
                player = str(outcome.get("description") or "").strip()
                point = outcome.get("point")
                price = outcome.get("price")
                if not player or point is None or price is None:
                    continue
                try:
                    line = float(point)
                    american = int(price)
                except (TypeError, ValueError):
                    continue
                if not is_valid_american_odds(american):
                    continue
                key = (player, market_key, line, name)
                grouped.setdefault(key, []).append(american)

    by_player_market: dict[tuple[str, str, float], dict[str, Any]] = {}
    for (player, market_key, line, side), prices in grouped.items():
        pk = (player, market_key, line)
        row = by_player_market.setdefault(
            pk,
            {
                "player": player,
                "market_type": market_key,
                "market_label": market_label(market_key),
                "line": line,
                "over_odds": None,
                "under_odds": None,
            },
        )
        med = _median_int(prices)
        if side == "over":
            row["over_odds"] = med
        else:
            row["under_odds"] = med

    rows = [r for r in by_player_market.values() if r.get("over_odds") or r.get("under_odds")]
    rows.sort(key=lambda r: (r["market_type"], r["player"], r["line"]))
    return rows


def _resolve_event_id(
    game_date: date,
    home_team: str,
    away_team: str,
    *,
    refresh: bool = False,
) -> tuple[str | None, str | None]:
    cache_path = _events_cache_path(game_date)
    age = _cache_age_seconds(cache_path)
    cached = _load_json(cache_path) if not refresh else None
    if cached and age is not None and age < DEFAULT_CACHE_TTL_SECONDS:
        events = cached.get("events") or []
    else:
        result = fetch_mlb_events_if_allowed()
        if result.denied:
            return None, result.denied_reason or "quota_denied"
        if result.error:
            return None, result.error
        events = result.events or []
        _write_json(
            cache_path,
            {
                "date": game_date.isoformat(),
                "fetched_at": _clock().isoformat(),
                "events": events,
            },
        )

    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    for event in events:
        if (
            normalize_team_name(event.get("home_team", "")) == home
            and normalize_team_name(event.get("away_team", "")) == away
        ):
            eid = event.get("id")
            return (str(eid) if eid else None), None
    return None, "no_matching_event"


def _probable_pitchers(game_date: date, game_id: str) -> tuple[str | None, str | None]:
    for away_p, home_p, gid in _day_probable_pitchers(game_date):
        if gid == str(game_id):
            return away_p, home_p
    return None, None


def _day_probable_pitchers(game_date: date) -> list[tuple[str | None, str | None, str]]:
    """One schedule fetch per day — reused across all games in a slate scan."""
    cache_path = PROPS_DIR / f"pitchers_{game_date.isoformat()}.json"
    age = _cache_age_seconds(cache_path)
    if age is not None and age < DEFAULT_CACHE_TTL_SECONDS and cache_path.exists():
        try:
            rows = json.loads(cache_path.read_text(encoding="utf-8")).get("games") or []
            return [
                (r.get("away"), r.get("home"), str(r.get("game_id")))
                for r in rows
            ]
        except (json.JSONDecodeError, OSError):
            pass

    rows: list[tuple[str | None, str | None, str]] = []
    try:
        api_games = filter_board_games(fetch_mlb_schedule_day(game_date), game_date)
        serializable: list[dict[str, Any]] = []
        for g in api_games:
            gid = str(g.get("gamePk"))
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            away_p = (away.get("probablePitcher") or {}).get("fullName")
            home_p = (home.get("probablePitcher") or {}).get("fullName")
            rows.append((away_p, home_p, gid))
            serializable.append({"game_id": gid, "away": away_p, "home": home_p})
        _write_json(
            cache_path,
            {"date": game_date.isoformat(), "games": serializable},
        )
    except Exception as exc:
        logger.debug("Could not load probable pitchers for %s: %s", game_date, exc)
    return rows


def _opposing_pitcher_for_prop(
    player: str,
    market_type: str,
    *,
    away_pitcher: str | None,
    home_pitcher: str | None,
    away_team_id: int | None,
    home_team_id: int | None,
    player_team_id: int | None = None,
) -> str | None:
    if not market_type.startswith("batter_"):
        return None
    team_id = player_team_id
    if team_id is None:
        player_id = _search_player_id(player)
        if player_id is not None:
            team_id = _player_team_id(player_id)
    if team_id is None:
        return home_pitcher or away_pitcher
    if home_team_id is not None and team_id == int(home_team_id):
        return away_pitcher
    if away_team_id is not None and team_id == int(away_team_id):
        return home_pitcher
    return home_pitcher or away_pitcher


def _enrich_props(
    props: list[dict[str, Any]],
    *,
    season: int,
    away_pitcher: str | None,
    home_pitcher: str | None,
    away_team_id: int | None,
    home_team_id: int | None,
) -> list[dict[str, Any]]:
    if len(props) > MAX_PROP_LINES_TO_SCORE:
        props = props[:MAX_PROP_LINES_TO_SCORE]

    batter_players = {
        row["player"]
        for row in props
        if str(row.get("market_type", "")).startswith("batter_")
    }
    warm_scoring_cache(batter_players, props, season)

    player_team_ids: dict[str, int | None] = {}
    for name in batter_players:
        pid = _search_player_id(name)
        player_team_ids[name] = _player_team_id(pid) if pid else None

    enriched: list[dict[str, Any]] = []
    for row in props:
        opposing = _opposing_pitcher_for_prop(
            row["player"],
            row["market_type"],
            away_pitcher=away_pitcher,
            home_pitcher=home_pitcher,
            away_team_id=away_team_id,
            home_team_id=home_team_id,
            player_team_id=player_team_ids.get(row["player"]),
        )
        analysis = score_prop(
            player=row["player"],
            market_type=row["market_type"],
            line=float(row["line"]),
            over_odds=row.get("over_odds"),
            under_odds=row.get("under_odds"),
            season=season,
            opposing_pitcher=opposing,
        )
        merged = {**row, **analysis}
        enriched.append(merged)
    enriched.sort(
        key=lambda r: (
            not r.get("actionable"),
            r.get("recommended_hit_rate") is None,
            prop_rank_key(r),
            r["player"],
        ),
    )
    return enriched


def build_game_props(
    game_id: str,
    game_date: date | None = None,
    *,
    refresh: bool = False,
    markets: str = DEFAULT_MLB_PROP_MARKETS,
) -> dict[str, Any] | None:
    """Fetch + score player props for one MLB game."""
    game_date = game_date or date.today()
    detail = get_mlb_game(game_id, game_date)
    if detail is None:
        return None

    game = detail["game"]
    home_team = game["home_team"]
    away_team = game["away_team"]
    season = game_date.year
    cache_path = _props_cache_path(game_id)
    age = _cache_age_seconds(cache_path)

    if not refresh:
        cached = _load_cached_game_props(str(game_id))
        if cached:
            if age is not None and age >= DEFAULT_CACHE_TTL_SECONDS:
                cached = {**cached, "stale_cache": True}
            return _mark_stale_props(cached)

    empty_payload: dict[str, Any] = {
        "game_id": str(game_id),
        "date": game_date.isoformat(),
        "matchup": f"{away_team} @ {home_team}",
        "home_team": home_team,
        "away_team": away_team,
        "props": [],
        "top_picks": [],
        "fetched_at": None,
        "source": None,
        "status": "empty",
        "message": None,
        "markets_requested": markets,
    }

    event_id, event_err = _resolve_event_id(
        game_date, home_team, away_team, refresh=refresh
    )
    if not event_id:
        msg = _status_message(event_err)
        empty_payload["message"] = msg
        return empty_payload

    result = fetch_mlb_event_props_if_allowed(event_id, markets=markets)
    if result.denied:
        empty_payload["message"] = _status_message(result.denied_reason)
        cached = _load_json(cache_path)
        if cached and cached.get("props"):
            cached["message"] = empty_payload["message"]
            return cached
        return empty_payload
    if result.error or not result.events:
        empty_payload["message"] = result.error or "props_unavailable"
        return empty_payload

    event = result.events[0]
    props = _parse_event_props(event)
    away_p, home_p = _probable_pitchers(game_date, game_id)
    enriched = _enrich_props(
        props,
        season=season,
        away_pitcher=away_p,
        home_pitcher=home_p,
        away_team_id=game.get("away_team_id"),
        home_team_id=game.get("home_team_id"),
    )
    top_picks = [
        p
        for p in enriched
        if prop_is_bettable(p) and p.get("score") is not None and p.get("score", 0) >= 60
    ][:12]

    payload = {
        **empty_payload,
        "props": enriched,
        "top_picks": top_picks,
        "fetched_at": _clock().isoformat(),
        "source": result.source,
        "status": "ok" if enriched else "empty",
        "message": None if enriched else "No player prop markets returned",
        "event_id": event_id,
    }
    _write_json(cache_path, payload)
    return payload


def _status_message(code: str | None) -> str:
    if code in (None, "no_matching_event"):
        return "No sportsbook event found for this matchup yet."
    if code == "live_odds_disabled":
        return "Live odds disabled — set USE_LIVE_ODDS=true and ODDS_API_KEY."
    if code == "quota_denied" or (code and "quota" in code):
        return "Odds API quota reached — showing cached lines if available."
    return str(code)


def evaluate_prop_parlay(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute combined parlay payout from American odds legs."""
    from app.odds.odds_math import (
        american_to_implied_prob,
        parlay_decimal_payout,
    )

    if not legs:
        return {
            "leg_count": 0,
            "decimal_payout": 1.0,
            "american_payout": None,
            "implied_prob": None,
            "profit_on_10": 0.0,
        }

    american_list: list[int] = []
    implied: list[float] = []
    for leg in legs:
        odds = leg.get("american_odds")
        if odds is None:
            continue
        american_list.append(int(odds))
        implied.append(american_to_implied_prob(int(odds)))

    decimal = parlay_decimal_payout(american_list)
    combined_implied = 1.0
    for p in implied:
        combined_implied *= p

    american_payout: int | None = None
    if decimal >= 2.0:
        american_payout = int(round((decimal - 1.0) * 100))
    elif decimal > 1.0:
        american_payout = int(round(-100.0 / (decimal - 1.0)))

    return {
        "leg_count": len(american_list),
        "decimal_payout": round(decimal, 4),
        "american_payout": american_payout,
        "implied_prob": round(combined_implied, 6),
        "profit_on_10": round((decimal * 10.0) - 10.0, 2),
    }


def _load_cached_game_props(game_id: str) -> dict[str, Any] | None:
    """Return on-disk game props whenever present (git-deployed cache may be older than TTL)."""
    payload = _load_json(_props_cache_path(game_id))
    if payload and payload.get("props"):
        return payload
    return None


def _load_best_slate_props(
    game_date: date,
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    """Today's slate aggregate only — no cross-day fallback for bettable props."""
    cached = _load_json(_slate_cache_path(game_date))
    if cached and cached.get("all_props") is not None:
        return cached["all_props"] or [], "slate_cache", cached
    return [], "none", None


def _aggregate_repo_game_props(game_date: date) -> list[dict[str, Any]]:
    """Merge bettable props from per-game cache files for the requested date."""
    picks: list[dict[str, Any]] = []
    if not PROPS_DIR.exists():
        return picks
    for path in PROPS_DIR.glob("*.json"):
        if not path.stem.isdigit():
            continue
        payload = _load_json(path)
        if not payload:
            continue
        if payload.get("date") and payload.get("date") != game_date.isoformat():
            continue
        age = _cache_age_seconds(path)
        if age is not None and age >= DEFAULT_CACHE_TTL_SECONDS:
            payload = {**payload, "stale_cache": True}
        picks.extend(_collect_actionable_props(payload, path.stem))
    picks.sort(key=prop_rank_key)
    return picks


def _daily_props_payload(
    *,
    game_date: date,
    limit: int,
    picks: list[dict[str, Any]],
    source: str,
    games_on_slate: int = 0,
    games_scanned: int = 0,
    games_fetched: int = 0,
    fetch_cap_hit: bool = False,
    cached_at: str | None = None,
    hint: str | None = None,
    auto_scanned: bool = False,
) -> dict[str, Any]:
    from app.odds.live_odds import live_odds_enabled

    out: dict[str, Any] = {
        "date": game_date.isoformat(),
        "games_on_slate": games_on_slate,
        "games_scanned": games_scanned,
        "games_fetched": games_fetched,
        "fetch_cap_hit": fetch_cap_hit,
        "total_actionable": len(picks),
        "top_props": picks[:limit],
        "source": source,
        "cached_at": cached_at,
        "disclaimer": (
            "Ranked by hit rate on the recommended side (L10, then L5, then season) "
            "— experimental, not betting advice."
        ),
        "live_odds_enabled": live_odds_enabled(),
        "auto_scanned": auto_scanned,
    }
    if hint:
        out["hint"] = hint
    return out


def build_daily_top_props(
    game_date: date | None = None,
    *,
    limit: int = 12,
    scan: bool = False,
) -> dict[str, Any]:
    """
    Aggregate actionable props across today's slate.

    When scan=True, fetch props for games missing cache (quota-gated, capped).
    Serves git-deployed cache even when older than TTL so VPS deploys work offline.
    """
    game_date = game_date or date.today()

    if not scan:
        picks, source, meta = _load_best_slate_props(game_date)
        if picks:
            picks = [p for p in picks if prop_is_bettable(p)]
            picks.sort(key=prop_rank_key)
            return _daily_props_payload(
                game_date=game_date,
                limit=limit,
                picks=picks,
                source=source,
                games_on_slate=meta.get("games_on_slate", 0) if meta else 0,
                games_scanned=meta.get("games_scanned", 0) if meta else 0,
                cached_at=meta.get("cached_at") if meta else None,
            )

    schedule = get_mlb_schedule(game_date)
    games = schedule.get("games") or []
    fetch_limit = _max_slate_prop_fetch(len(games))
    if scan:
        _day_probable_pitchers(game_date)

    picks: list[dict[str, Any]] = []
    games_scanned = 0
    games_fetched = 0
    fetch_cap_hit = False
    fetch_errors: list[str] = []

    for game in games:
        gid = str(game.get("game_id"))
        if not gid:
            continue
        cached_payload = _load_cached_game_props(gid)
        payload = cached_payload

        if scan and not cached_payload:
            if games_fetched >= fetch_limit:
                fetch_cap_hit = True
                continue
            built = build_game_props(gid, game_date=game_date, refresh=True)
            if built:
                payload = built
                games_fetched += 1
                msg = built.get("message")
                if msg and not built.get("props"):
                    fetch_errors.append(msg)
        elif not payload or not payload.get("props"):
            continue

        games_scanned += 1
        picks.extend(_collect_actionable_props(payload, gid))

    source = "scan" if scan else "game_cache"
    hint: str | None = None

    if not picks:
        picks = _aggregate_repo_game_props(game_date)
        if picks:
            source = "repo_game_cache"
            hint = "Using today's cached game props from disk."
        elif fetch_errors:
            hint = fetch_errors[0]
        elif not games:
            hint = "No MLB games on today's schedule."
        else:
            hint = "No props cached yet — enable USE_LIVE_ODDS and ODDS_API_KEY, then scan."

    picks.sort(key=prop_rank_key)
    if picks and scan:
        _write_json(
            _slate_cache_path(game_date),
            {
                "date": game_date.isoformat(),
                "cached_at": _clock().isoformat(),
                "games_on_slate": len(games),
                "games_scanned": games_scanned,
                "games_fetched": games_fetched,
                "all_props": picks,
            },
        )

    return _daily_props_payload(
        game_date=game_date,
        limit=limit,
        picks=picks,
        source=source,
        games_on_slate=len(games),
        games_scanned=games_scanned,
        games_fetched=games_fetched,
        fetch_cap_hit=fetch_cap_hit,
        hint=hint,
    )
