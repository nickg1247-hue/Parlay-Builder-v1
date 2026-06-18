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
from app.odds.the_odds_api import (
    ALTERNATE_MLB_PROP_MARKETS,
    DEFAULT_MLB_PROP_MARKETS,
)
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


def _raw_events_dir() -> Path:
    return PROPS_DIR / "raw_events"
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("PROPS_CACHE_TTL_SECONDS", "7200"))
MAX_PROP_LINES_TO_SCORE = int(os.getenv("MAX_PROP_LINES_TO_SCORE", "80"))
RUNS_PROP_MARKET = "batter_runs_scored"
DEFAULT_PROP_BOOKMAKER = "consensus"
# Keys must match The Odds API bookmaker keys (see the-odds-api.com bookmaker list).
PROP_BOOKMAKERS: dict[str, str] = {
    "consensus": "Best line (median)",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "betrivers": "BetRivers",
    "williamhill_us": "Caesars",
    "bovada": "Bovada",
    "betonlineag": "BetOnline",
    "espnbet": "theScore Bet",
    "fanatics": "Fanatics",
}
# Legacy/wrong keys from early UI — map to valid API keys.
BOOKMAKER_ALIASES: dict[str, str] = {
    "caesars": "williamhill_us",
    "williamhill": "williamhill_us",
    "thescore": "espnbet",
    "pointsbetus": DEFAULT_PROP_BOOKMAKER,
    "pointsbet": DEFAULT_PROP_BOOKMAKER,
}


def _discover_cached_prop_bookmakers() -> set[str]:
    books: set[str] = set()
    if not PROPS_DIR.exists():
        return books
    for path in PROPS_DIR.glob("*.*.json"):
        stem = path.stem
        if "." not in stem:
            continue
        _, book = stem.rsplit(".", 1)
        if book == DEFAULT_PROP_BOOKMAKER:
            continue
        payload = _load_json(path)
        if payload and payload.get("props"):
            books.add(book)
    return books


def _discover_live_prop_bookmakers() -> set[str]:
    """Books seen in cached raw Odds API event payloads (all regions, one fetch)."""
    books: set[str] = set()
    raw_dir = _raw_events_dir()
    if not raw_dir.exists():
        return books
    for path in raw_dir.glob("*.json"):
        data = _load_json(path)
        event = (data or {}).get("event")
        if event:
            books.update(_books_with_prop_markets(event))
    return books


def list_prop_bookmakers() -> list[dict[str, Any]]:
    cached = _discover_cached_prop_bookmakers()
    live = _discover_live_prop_bookmakers()
    prop_ready = cached | live
    return [
        {
            "key": key,
            "label": label,
            "has_cache": key == DEFAULT_PROP_BOOKMAKER or key in cached,
            "has_props": key == DEFAULT_PROP_BOOKMAKER or key in prop_ready,
        }
        for key, label in PROP_BOOKMAKERS.items()
    ]


def _normalize_bookmaker(raw: str | None) -> str:
    key = (raw or DEFAULT_PROP_BOOKMAKER).strip().lower()
    if not key:
        return DEFAULT_PROP_BOOKMAKER
    key = BOOKMAKER_ALIASES.get(key, key)
    if key == DEFAULT_PROP_BOOKMAKER or key in PROP_BOOKMAKERS:
        return key
    if key.replace("_", "").isalnum():
        return key
    return DEFAULT_PROP_BOOKMAKER


def _bookmaker_label(bookmaker: str) -> str:
    return PROP_BOOKMAKERS.get(bookmaker, bookmaker.replace("_", " ").title())


def _books_with_prop_markets(event: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for book in event.get("bookmakers") or []:
        book_key = str(book.get("key") or "")
        if not book_key:
            continue
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or "")
            if market_key.startswith(("batter_", "pitcher_")):
                keys.append(book_key)
                break
    return sorted(set(keys))


def _empty_book_message(
    book: str,
    *,
    available: list[str],
    parsed_count: int,
) -> str:
    label = _bookmaker_label(book)
    if book == DEFAULT_PROP_BOOKMAKER:
        if not available:
            return "No sportsbooks returned player prop markets for this game yet."
        if parsed_count == 0:
            return "Prop markets were returned but no lines could be parsed."
        return "No player prop markets returned"

    if book not in available:
        if available:
            alt = ", ".join(_bookmaker_label(k) for k in available[:5])
            return f"{label} has no MLB player props for this game. Books with props: {alt}."
        return f"{label} has no MLB player props for this game yet."

    if parsed_count == 0:
        return f"{label} returned prop markets but no lines could be parsed."
    return f"{label} props did not pass scoring filters."


def _include_runs_props(markets_requested: str) -> bool:
    if RUNS_PROP_MARKET in markets_requested:
        return True
    return os.getenv("PROP_INCLUDE_RUNS", "").strip().lower() in ("1", "true", "yes")


def _filter_prop_markets(
    props: list[dict[str, Any]],
    *,
    markets_requested: str,
) -> list[dict[str, Any]]:
    if _include_runs_props(markets_requested):
        return props
    return [row for row in props if row.get("market_type") != RUNS_PROP_MARKET]


def _trim_props_payload(payload: dict[str, Any], markets_requested: str) -> dict[str, Any]:
    props = _filter_prop_markets(payload.get("props") or [], markets_requested=markets_requested)
    top_picks = [
        p
        for p in props
        if p.get("actionable") and p.get("score") is not None and p.get("score", 0) >= 60
    ][:12]
    total_actionable = sum(
        1
        for p in props
        if p.get("actionable")
        and p.get("recommended_hit_rate") is not None
        and p.get("recommended_odds") is not None
    )
    out = {**payload, "props": props, "top_picks": top_picks}
    if "total_actionable" in payload:
        out["total_actionable"] = total_actionable
    return out


def list_prop_market_types() -> list[dict[str, str]]:
    from app.services.prop_scoring import MARKET_LABELS

    keys = (
        "batter_hits",
        "batter_total_bases",
        "batter_home_runs",
        "batter_rbis",
        "pitcher_strikeouts",
    )
    return [{"key": key, "label": MARKET_LABELS.get(key, key)} for key in keys]


def _canonical_market_type(market_key: str) -> tuple[str, str]:
    """Return (canonical market type, line_kind)."""
    if market_key.endswith("_alternate"):
        return market_key[: -len("_alternate")], "alternate"
    return market_key, "main"


def _markets_for_fetch(*, include_alternates: bool) -> str:
    if include_alternates:
        return f"{DEFAULT_MLB_PROP_MARKETS},{ALTERNATE_MLB_PROP_MARKETS}"
    return DEFAULT_MLB_PROP_MARKETS


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


def prop_slip_leg(
    prop: dict[str, Any],
    *,
    game_id: str,
    matchup: str | None,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> dict[str, Any]:
    """Normalize a ranked prop row for the client bet slip."""
    side = prop.get("recommended_side") or "over"
    leg_parts = [
        str(game_id),
        str(prop.get("player", "")),
        str(prop.get("market_type", "")),
        str(prop.get("line", "")),
        side,
    ]
    if bookmaker and bookmaker != DEFAULT_PROP_BOOKMAKER:
        leg_parts.append(bookmaker)
    return {
        "id": "|".join(leg_parts),
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
        "bookmaker": bookmaker,
        "bookmaker_label": _bookmaker_label(bookmaker),
    }


def _collect_actionable_props(payload: dict[str, Any], game_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    matchup = payload.get("matchup")
    bookmaker = _normalize_bookmaker(payload.get("bookmaker"))
    markets_requested = str(payload.get("markets_requested") or DEFAULT_MLB_PROP_MARKETS)
    for prop in _filter_prop_markets(
        payload.get("props") or [],
        markets_requested=markets_requested,
    ):
        if not prop.get("actionable") or prop.get("recommended_hit_rate") is None:
            continue
        if prop.get("recommended_odds") is None:
            continue
        rows.append(
            {
                **prop,
                "game_id": game_id,
                "matchup": matchup,
                "bookmaker": bookmaker,
                "bookmaker_label": payload.get("bookmaker_label") or _bookmaker_label(bookmaker),
                "slip_leg": prop_slip_leg(
                    prop, game_id=game_id, matchup=matchup, bookmaker=bookmaker
                ),
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


def _props_cache_path(game_id: str, bookmaker: str = DEFAULT_PROP_BOOKMAKER) -> Path:
    book = _normalize_bookmaker(bookmaker)
    return PROPS_DIR / f"{game_id}.{book}.json"


def _events_cache_path(game_date: date) -> Path:
    return EVENTS_DIR / f"{game_date.isoformat()}.json"


def _slate_cache_path(game_date: date, bookmaker: str = DEFAULT_PROP_BOOKMAKER) -> Path:
    book = _normalize_bookmaker(bookmaker)
    return PROPS_DIR / f"slate_{game_date.isoformat()}.{book}.json"


def _raw_event_cache_path(game_id: str, game_date: date) -> Path:
    return _raw_events_dir() / f"{game_id}.{game_date.isoformat()}.json"


def _load_raw_event(game_id: str, game_date: date) -> dict[str, Any] | None:
    return _load_json(_raw_event_cache_path(game_id, game_date))


def _save_raw_event(
    game_id: str,
    game_date: date,
    *,
    event: dict[str, Any],
    event_id: str,
    markets: str,
    source: str,
) -> None:
    _write_json(
        _raw_event_cache_path(game_id, game_date),
        {
            "game_id": str(game_id),
            "date": game_date.isoformat(),
            "event_id": event_id,
            "markets": markets,
            "source": source,
            "fetched_at": _clock().isoformat(),
            "event": event,
        },
    )


def _raw_event_fresh(raw: dict[str, Any]) -> bool:
    fetched = raw.get("fetched_at")
    if not fetched:
        return False
    try:
        ts = datetime.fromisoformat(str(fetched))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_clock() - ts).total_seconds() < DEFAULT_CACHE_TTL_SECONDS


def _assemble_game_props_payload(
    event: dict[str, Any],
    *,
    game_id: str,
    game_date: date,
    game: dict[str, Any],
    book: str,
    fetch_markets: str,
    event_id: str | None,
    source: str,
    empty_payload: dict[str, Any],
) -> dict[str, Any]:
    home_team = game["home_team"]
    away_team = game["away_team"]
    season = game_date.year
    available_books = _books_with_prop_markets(event)
    props = _filter_prop_markets(
        _parse_event_props(event, bookmaker_key=book),
        markets_requested=fetch_markets,
    )
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
        if p.get("actionable") and p.get("score") is not None and p.get("score", 0) >= 60
    ][:12]
    return {
        **empty_payload,
        "props": enriched,
        "top_picks": top_picks,
        "fetched_at": _clock().isoformat(),
        "source": source,
        "status": "ok" if enriched else "empty",
        "message": (
            None
            if enriched
            else _empty_book_message(
                book,
                available=available_books,
                parsed_count=len(props),
            )
        ),
        "event_id": event_id,
        "available_bookmakers": [
            {"key": key, "label": _bookmaker_label(key)} for key in available_books
        ],
    }


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


def _parse_event_props(
    event: dict[str, Any],
    bookmaker_key: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize Odds API event-odds response to prop rows."""
    bookmaker_key = _normalize_bookmaker(bookmaker_key)
    books = event.get("bookmakers") or []
    if bookmaker_key != DEFAULT_PROP_BOOKMAKER:
        books = [book for book in books if book.get("key") == bookmaker_key]

    grouped: dict[tuple[str, str, float, str, str], list[int]] = {}
    for book in books:
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or "")
            if not market_key.startswith(("batter_", "pitcher_")):
                continue
            canonical_type, line_kind = _canonical_market_type(market_key)
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
                key = (player, canonical_type, line, name, line_kind)
                grouped.setdefault(key, []).append(american)

    by_player_market: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    for (player, market_key, line, side, line_kind), prices in grouped.items():
        pk = (player, market_key, line, line_kind)
        row = by_player_market.setdefault(
            pk,
            {
                "player": player,
                "market_type": market_key,
                "market_label": market_label(market_key),
                "line": line,
                "line_kind": line_kind,
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
    markets: str | None = None,
    include_alternates: bool = False,
    bookmaker: str | None = None,
) -> dict[str, Any] | None:
    """Fetch + score player props for one MLB game."""
    fetch_markets = markets or _markets_for_fetch(include_alternates=include_alternates)
    game_date = game_date or date.today()
    book = _normalize_bookmaker(bookmaker)
    detail = get_mlb_game(game_id, game_date)
    if detail is None:
        return None

    game = detail["game"]
    home_team = game["home_team"]
    away_team = game["away_team"]
    season = game_date.year
    cache_path = _props_cache_path(game_id, book)
    age = _cache_age_seconds(cache_path)

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
        "markets_requested": fetch_markets,
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
    }

    if not refresh:
        cached = _load_cached_game_props(str(game_id), book)
        if cached:
            cached = _trim_props_payload(cached, fetch_markets)
            if age is not None and age >= DEFAULT_CACHE_TTL_SECONDS:
                cached = {**cached, "stale_cache": True}
            return cached
        raw = _load_raw_event(str(game_id), game_date)
        if raw and _raw_event_fresh(raw) and raw.get("event"):
            payload = _assemble_game_props_payload(
                raw["event"],
                game_id=str(game_id),
                game_date=game_date,
                game=game,
                book=book,
                fetch_markets=raw.get("markets") or fetch_markets,
                event_id=raw.get("event_id"),
                source="raw_event_cache",
                empty_payload=empty_payload,
            )
            _write_json(cache_path, payload)
            return payload

    event_id, event_err = _resolve_event_id(
        game_date, home_team, away_team, refresh=refresh
    )
    if not event_id:
        msg = _status_message(event_err)
        empty_payload["message"] = msg
        return empty_payload

    # Always fetch all US books in one call; filter to the selected book when parsing.
    result = fetch_mlb_event_props_if_allowed(event_id, markets=fetch_markets)
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
    _save_raw_event(
        str(game_id),
        game_date,
        event=event,
        event_id=event_id,
        markets=fetch_markets,
        source=result.source or "the_odds_api_live",
    )
    payload = _assemble_game_props_payload(
        event,
        game_id=str(game_id),
        game_date=game_date,
        game=game,
        book=book,
        fetch_markets=fetch_markets,
        event_id=event_id,
        source=result.source or "the_odds_api_live",
        empty_payload=empty_payload,
    )
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


def _load_cached_game_props(
    game_id: str,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> dict[str, Any] | None:
    """Return on-disk game props whenever present (git-deployed cache may be older than TTL)."""
    book = _normalize_bookmaker(bookmaker)
    payload = _load_json(_props_cache_path(game_id, book))
    if payload and payload.get("props"):
        return payload
    if book == DEFAULT_PROP_BOOKMAKER:
        legacy = _load_json(PROPS_DIR / f"{game_id}.json")
        if legacy and legacy.get("props"):
            return legacy
    return None


def _load_best_slate_props(
    game_date: date,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    """Today's slate aggregate, else newest slate_*.json in the repository."""
    book = _normalize_bookmaker(bookmaker)
    cached = _load_json(_slate_cache_path(game_date, book))
    if cached and cached.get("all_props") is not None:
        return cached["all_props"] or [], "slate_cache", cached
    if book == DEFAULT_PROP_BOOKMAKER:
        legacy = _load_json(PROPS_DIR / f"slate_{game_date.isoformat()}.json")
        if legacy and legacy.get("all_props") is not None:
            return legacy["all_props"] or [], "slate_cache", legacy

    suffix = f".{book}.json"
    candidates = sorted(
        (
            path
            for path in PROPS_DIR.glob("slate_*.json")
            if path.name.endswith(suffix)
            or (book == DEFAULT_PROP_BOOKMAKER and path.name.count(".") == 1)
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        data = _load_json(path)
        if data and data.get("all_props"):
            return data["all_props"] or [], "slate_cache_repo", data
    return [], "none", None


def _aggregate_repo_game_props(
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> list[dict[str, Any]]:
    """Last resort: merge actionable props from all per-game JSON files in the repo."""
    book = _normalize_bookmaker(bookmaker)
    picks: list[dict[str, Any]] = []
    if not PROPS_DIR.exists():
        return picks
    seen: set[str] = set()
    for path in PROPS_DIR.glob("*.json"):
        stem = path.stem
        if stem.isdigit():
            if book != DEFAULT_PROP_BOOKMAKER:
                continue
            game_id = stem
        elif "." in stem:
            game_id, cached_book = stem.rsplit(".", 1)
            if not game_id.isdigit() or cached_book != book:
                continue
        else:
            continue
        if game_id in seen:
            continue
        payload = _load_json(path)
        if not payload:
            continue
        seen.add(game_id)
        picks.extend(_collect_actionable_props(payload, game_id))
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
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> dict[str, Any]:
    from app.odds.live_odds import live_odds_enabled

    book = _normalize_bookmaker(bookmaker)
    out: dict[str, Any] = {
        "date": game_date.isoformat(),
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
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
    refresh: bool = False,
    bookmaker: str | None = None,
    include_alternates: bool = False,
) -> dict[str, Any]:
    """
    Aggregate actionable props across today's slate.

    When scan=True, fetch props for games missing cache (quota-gated, capped).
    Serves git-deployed cache even when older than TTL so VPS deploys work offline.
    """
    game_date = game_date or date.today()
    book = _normalize_bookmaker(bookmaker)
    if refresh:
        scan = True

    if not scan and not refresh:
        picks, source, meta = _load_best_slate_props(game_date, book)
        if picks:
            picks = _filter_prop_markets(picks, markets_requested=DEFAULT_MLB_PROP_MARKETS)
            picks.sort(key=prop_rank_key)
            return _daily_props_payload(
                game_date=game_date,
                limit=limit,
                picks=picks,
                source=source,
                games_on_slate=meta.get("games_on_slate", 0) if meta else 0,
                games_scanned=meta.get("games_scanned", 0) if meta else 0,
                cached_at=meta.get("cached_at") if meta else None,
                bookmaker=book,
                hint=(
                    "Showing cached slate props from repository."
                    if source == "slate_cache_repo"
                    else None
                ),
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
        cached_payload = None if refresh else _load_cached_game_props(gid, book)
        if cached_payload:
            cached_payload = _trim_props_payload(cached_payload, DEFAULT_MLB_PROP_MARKETS)
        payload = cached_payload

        if scan and (refresh or not cached_payload):
            if games_fetched >= fetch_limit:
                fetch_cap_hit = True
                continue
            built = build_game_props(
                gid,
                game_date=game_date,
                refresh=True,
                bookmaker=book,
                include_alternates=include_alternates,
            )
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
        picks = _aggregate_repo_game_props(book)
        if picks:
            source = "repo_game_cache"
            hint = "Using props from on-disk game cache (schedule date may differ from cache)."
        elif fetch_errors:
            hint = fetch_errors[0]
        elif not games:
            hint = "No MLB games on today's schedule."
        else:
            hint = "No props cached yet — enable USE_LIVE_ODDS and ODDS_API_KEY, then scan."

    picks.sort(key=prop_rank_key)
    if picks and scan:
        _write_json(
            _slate_cache_path(game_date, book),
            {
                "date": game_date.isoformat(),
                "bookmaker": book,
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
        bookmaker=book,
    )


def _collect_scored_props_from_payload(
    payload: dict[str, Any],
    game_id: str,
) -> list[dict[str, Any]]:
    bookmaker = _normalize_bookmaker(payload.get("bookmaker"))
    book_label = payload.get("bookmaker_label") or _bookmaker_label(bookmaker)
    matchup = payload.get("matchup")
    rows: list[dict[str, Any]] = []
    for prop in payload.get("props") or []:
        if not prop.get("over_odds") and not prop.get("under_odds"):
            continue
        row = {
            **prop,
            "game_id": game_id,
            "matchup": matchup,
            "bookmaker": bookmaker,
            "bookmaker_label": book_label,
            "line_kind": prop.get("line_kind") or "main",
        }
        if prop.get("actionable") and prop.get("recommended_odds") is not None:
            row["slip_leg"] = prop_slip_leg(
                prop, game_id=game_id, matchup=matchup, bookmaker=bookmaker
            )
        rows.append(row)
    return rows


def _passes_prop_search_filters(
    prop: dict[str, Any],
    *,
    market_type: str | None,
    min_odds: int | None,
    line_kind: str | None,
    line_value: float | None,
    actionable_only: bool,
) -> bool:
    if actionable_only and not prop.get("actionable"):
        return False
    if market_type and prop.get("market_type") != market_type:
        return False
    kind = prop.get("line_kind") or "main"
    if line_kind and line_kind not in (None, "", "both"):
        if line_kind == "main" and kind != "main":
            return False
        if line_kind == "alternate" and kind != "alternate":
            return False
    if line_value is not None:
        try:
            if float(prop.get("line")) != float(line_value):
                return False
        except (TypeError, ValueError):
            return False
    if min_odds is not None:
        odds = prop.get("recommended_odds")
        if odds is None:
            side = prop.get("recommended_side") or "over"
            odds = prop.get("over_odds") if side == "over" else prop.get("under_odds")
        if odds is None or int(odds) < int(min_odds):
            return False
    return True


def search_daily_props(
    game_date: date | None = None,
    *,
    bookmaker: str | None = None,
    market_type: str | None = None,
    min_odds: int | None = None,
    line_kind: str | None = None,
    line_value: float | None = None,
    actionable_only: bool = False,
    limit: int = 100,
    scan: bool = False,
    refresh: bool = False,
    include_alternates: bool = False,
) -> dict[str, Any]:
    """Filter scored props across the slate for the props explorer page."""
    game_date = game_date or date.today()
    book = _normalize_bookmaker(bookmaker)
    if refresh:
        scan = True
    if include_alternates or line_kind == "alternate":
        include_alternates = True

    base = build_daily_top_props(
        game_date,
        limit=max(limit, 500),
        scan=scan,
        refresh=refresh,
        bookmaker=book,
        include_alternates=include_alternates,
    )

    pool: list[dict[str, Any]] = []
    markets = _markets_for_fetch(include_alternates=include_alternates)
    schedule = get_mlb_schedule(game_date)
    for game in schedule.get("games") or []:
        gid = str(game.get("game_id") or "")
        if not gid:
            continue
        payload = _load_cached_game_props(gid, book)
        if not payload:
            raw = _load_raw_event(gid, game_date)
            if raw and _raw_event_fresh(raw) and raw.get("event"):
                payload = build_game_props(
                    gid,
                    game_date=game_date,
                    refresh=False,
                    bookmaker=book,
                    include_alternates=include_alternates,
                )
        if not payload:
            continue
        payload = _trim_props_payload(payload, markets)
        pool.extend(_collect_scored_props_from_payload(payload, gid))

    if not pool:
        picks, _, _ = _load_best_slate_props(game_date, book)
        pool = picks or base.get("top_props") or []

    filtered = [
        prop
        for prop in pool
        if _passes_prop_search_filters(
            prop,
            market_type=market_type,
            min_odds=min_odds,
            line_kind=line_kind,
            line_value=line_value,
            actionable_only=actionable_only,
        )
    ]
    filtered.sort(key=prop_rank_key)

    hint = base.get("hint")
    if line_kind == "alternate" and not include_alternates:
        hint = "Alternate lines require a refresh with alternates enabled."

    return {
        "date": game_date.isoformat(),
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
        "market_types": list_prop_market_types(),
        "total_matched": len(filtered),
        "props": filtered[:limit],
        "source": base.get("source"),
        "hint": hint,
        "live_odds_enabled": base.get("live_odds_enabled"),
        "filters": {
            "market_type": market_type,
            "min_odds": min_odds,
            "line_kind": line_kind or "both",
            "line_value": line_value,
            "actionable_only": actionable_only,
            "include_alternates": include_alternates,
        },
    }
