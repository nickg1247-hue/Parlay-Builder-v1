"""MLB player props: Odds API lines + recent-form scoring."""

from __future__ import annotations

import json
import logging
import os
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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
    EXTENDED_MLB_PROP_MARKETS,
)
from app.parlay.slate import fetch_mlb_schedule_day, filter_board_games
from app.services.prop_scoring import (
    _player_team_id,
    _search_player_id,
    is_perfect_l5_l10_season,
    market_label,
    refresh_prop_line_strength,
    score_prop,
    side_form_hit_rates,
    warm_scoring_cache,
)
from app.services.schedule_mlb import get_mlb_game, get_mlb_schedule

logger = logging.getLogger(__name__)

PROPS_DIR = PROJECT_ROOT / "data" / "processed" / "props_repository"
EVENTS_DIR = PROPS_DIR / "events"
PROPS_CACHE_META_PATH = PROPS_DIR / ".cache_meta.json"
# Bump when prop line parsing or cache rules change — triggers wipe on server start.
PROPS_CACHE_GENERATION = "20260620c"


def _raw_events_dir() -> Path:
    return PROPS_DIR / "raw_events"


DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("PROPS_CACHE_TTL_SECONDS", "7200"))
MAX_PROP_LINES_TO_SCORE = int(os.getenv("MAX_PROP_LINES_TO_SCORE", "80"))
RUNS_PROP_MARKET = "batter_runs_scored"
DEFAULT_PROP_BOOKMAKER = "consensus"
DEFAULT_DISPLAY_BOOKMAKER = "draftkings"
# Major US retail books only — excludes ladder-heavy books (Hard Rock, Fliff, etc.).
CONSENSUS_PROP_BOOKS = frozenset(
    {
        "draftkings",
        "fanduel",
        "betmgm",
        "betrivers",
        "williamhill_us",
        "bovada",
        "betonlineag",
        "espnbet",
        "fanatics",
    }
)
VERY_STRONG_LINE_STRENGTH = "very_strong"
# Keys must match The Odds API bookmaker keys (see the-odds-api.com bookmaker list).
PROP_BOOKMAKERS: dict[str, str] = {
    "consensus": "Best line (full markets only)",
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


def _resolve_bookmaker(raw: str | None) -> str:
    """Book used when the client omits bookmaker (display default: DraftKings)."""
    if raw is None or not str(raw).strip():
        return DEFAULT_DISPLAY_BOOKMAKER
    return _normalize_bookmaker(raw)


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


def is_very_strong_prop(prop: dict[str, Any]) -> bool:
    if prop.get("line_strength") == VERY_STRONG_LINE_STRENGTH:
        return True
    if not prop.get("actionable"):
        return False
    l5, l10, season = side_form_hit_rates(prop)
    return is_perfect_l5_l10_season(l5, l10, season)


def _normalize_scored_props(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh very-strong labels from hit-rate fields (cached props may say 'Strong line')."""
    return [refresh_prop_line_strength(dict(prop)) for prop in props]


def _game_pick_lists(props: list[dict[str, Any]]) -> dict[str, Any]:
    """Split scored props into very-strong vs regular top picks."""
    very_strong: list[dict[str, Any]] = []
    top_picks: list[dict[str, Any]] = []
    total_actionable = 0
    for prop in props:
        if not prop.get("actionable"):
            continue
        if not prop_is_bettable(prop):
            continue
        if prop.get("recommended_hit_rate") is None or prop.get("recommended_odds") is None:
            continue
        total_actionable += 1
        if is_very_strong_prop(prop):
            very_strong.append(prop)
        elif prop.get("score") is not None and prop.get("score", 0) >= 60:
            top_picks.append(prop)
    very_strong.sort(key=prop_rank_key)
    top_picks.sort(key=prop_rank_key)
    return {
        "very_strong_picks": very_strong[:12],
        "top_picks": top_picks[:12],
        "total_very_strong": len(very_strong),
        "total_actionable": total_actionable,
    }


def _split_slate_props(
    picks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    very_strong = [p for p in picks if is_very_strong_prop(p)]
    regular = [p for p in picks if not is_very_strong_prop(p)]
    very_strong.sort(key=prop_rank_key)
    regular.sort(key=prop_rank_key)
    return very_strong, regular


def _filter_prop_markets(
    props: list[dict[str, Any]],
    *,
    markets_requested: str,
) -> list[dict[str, Any]]:
    if _include_runs_props(markets_requested):
        return props
    return [row for row in props if row.get("market_type") != RUNS_PROP_MARKET]


def _trim_props_payload(payload: dict[str, Any], markets_requested: str) -> dict[str, Any]:
    props = _normalize_scored_props(
        _filter_prop_markets(payload.get("props") or [], markets_requested=markets_requested)
    )
    pick_lists = _game_pick_lists(props)
    out = {
        **payload,
        "props": props,
        **pick_lists,
    }
    if "total_actionable" in payload:
        out["total_actionable"] = pick_lists["total_actionable"]
    return out


def list_prop_market_types() -> list[dict[str, str]]:
    from app.services.prop_scoring import MARKET_LABELS

    keys = list(MARKET_LABELS.keys())
    return [{"key": key, "label": MARKET_LABELS.get(key, key)} for key in keys]


def _canonical_market_type(market_key: str) -> tuple[str, str]:
    """Return (canonical market type, line_kind)."""
    if market_key.endswith("_alternate"):
        return market_key[: -len("_alternate")], "alternate"
    return market_key, "main"


def _markets_for_fetch(*, include_alternates: bool, include_all_markets: bool = False) -> str:
    base = EXTENDED_MLB_PROP_MARKETS if include_all_markets else DEFAULT_MLB_PROP_MARKETS
    if _include_runs_props(base):
        base = f"{base},{RUNS_PROP_MARKET}"
    if include_alternates:
        alts = ALTERNATE_MLB_PROP_MARKETS
        if include_all_markets:
            alts = (
                f"{alts},pitcher_hits_allowed_alternate,"
                "pitcher_earned_runs_alternate,pitcher_outs_alternate"
            )
        return f"{base},{alts}"
    return base


def _markets_satisfy_request(cached_markets: str, requested: str) -> bool:
    cached_set = {m.strip() for m in cached_markets.split(",") if m.strip()}
    needed = {m.strip() for m in requested.split(",") if m.strip()}
    return needed.issubset(cached_set)


def _sample_props_for_scoring(
    props: list[dict[str, Any]], max_lines: int
) -> list[dict[str, Any]]:
    if len(props) <= max_lines:
        return props
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in props:
        by_market[str(row.get("market_type") or "unknown")].append(row)
    markets = sorted(by_market.keys())
    out: list[dict[str, Any]] = []
    idx = 0
    while len(out) < max_lines:
        added = False
        for mk in markets:
            bucket = by_market[mk]
            if idx < len(bucket):
                out.append(bucket[idx])
                added = True
                if len(out) >= max_lines:
                    break
        if not added:
            break
        idx += 1
    return out


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
    game_date: date | str | None = None,
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
    alltime_key = "hit_rate_over_alltime" if side == "over" else "hit_rate_under_alltime"
    link_key = "over_link" if side == "over" else "under_link"
    if isinstance(game_date, date):
        game_date_str = game_date.isoformat()
    else:
        game_date_str = str(game_date)[:10] if game_date else None
    return {
        "id": "|".join(leg_parts),
        "game_id": str(game_id),
        "game_date": game_date_str,
        "matchup": matchup,
        "player": prop.get("player"),
        "player_id": prop.get("player_id"),
        "photo_url": prop.get("photo_url"),
        "market_type": prop.get("market_type"),
        "market_label": prop.get("market_label"),
        "side": side,
        "line": prop.get("line"),
        "american_odds": prop.get("recommended_odds"),
        "hit_rate": prop.get("recommended_hit_rate"),
        "hit_rate_alltime": prop.get(alltime_key),
        "line_strength": prop.get("line_strength"),
        "line_strength_label": prop.get("line_strength_label"),
        "line_insight": prop.get("line_insight"),
        "score": prop.get("score"),
        "bookmaker": bookmaker,
        "bookmaker_label": _bookmaker_label(bookmaker),
        "deeplink": prop.get(link_key) or prop.get("deeplink"),
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
    if prop.get("complete_market") is not True:
        return False
    if prop.get("line_kind") == "alternate":
        return False
    if prop.get("primary_line") is False:
        return False
    if prop.get("stale_cache") and not allow_stale:
        return False
    return True


def get_props_refresh_meta(game_date: date | None = None) -> dict[str, Any]:
    """Latest props slate cache metadata for refresh status UI."""
    game_date = game_date or date.today()
    cached = _load_json(_slate_cache_path(game_date, DEFAULT_PROP_BOOKMAKER))
    if not cached:
        cached = _load_json(PROPS_DIR / f"slate_{game_date.isoformat()}.json")
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
    bookmaker = _normalize_bookmaker(payload.get("bookmaker"))
    markets_requested = str(payload.get("markets_requested") or DEFAULT_MLB_PROP_MARKETS)
    stale = bool(payload.get("stale_cache"))
    game_date_str = payload.get("date")
    try:
        game_date = (
            date.fromisoformat(str(game_date_str)) if game_date_str else date.today()
        )
    except ValueError:
        game_date = date.today()
    published = _load_published_index(str(game_id), game_date, bookmaker)
    for prop in _filter_prop_markets(
        payload.get("props") or [],
        markets_requested=markets_requested,
    ):
        row = dict(prop)
        if stale:
            row["stale_cache"] = True
        if not prop_is_bettable(row):
            continue
        published_key = _prop_published_key(row)
        if published is not None:
            if published_key is not None:
                if published_key not in published:
                    continue
            elif not _prop_matches_published(row, published):
                continue
        offered = row.get("offered_books") or []
        if (
            bookmaker not in (DEFAULT_PROP_BOOKMAKER, "consensus")
            and offered
            and bookmaker not in offered
        ):
            continue
        if row.get("recommended_hit_rate") is None:
            continue
        rows.append(
            {
                **row,
                "game_id": game_id,
                "matchup": matchup,
                "bookmaker": bookmaker,
                "bookmaker_label": payload.get("bookmaker_label") or _bookmaker_label(bookmaker),
                "slip_leg": prop_slip_leg(
                    row,
                    game_id=game_id,
                    matchup=matchup,
                    bookmaker=bookmaker,
                    game_date=game_date,
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


def _parse_cached_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _slate_cache_fresh(cached: dict[str, Any] | None, game_date: date) -> bool:
    """Slate aggregate is valid for this calendar day and within TTL."""
    if not cached:
        return False
    cached_day = str(cached.get("date") or "")
    if cached_day and cached_day != game_date.isoformat():
        return False
    ts = _parse_cached_timestamp(cached.get("cached_at"))
    if ts is not None:
        return (_clock() - ts).total_seconds() < DEFAULT_CACHE_TTL_SECONDS
    path = _slate_cache_path(
        game_date, _normalize_bookmaker(cached.get("bookmaker"))
    )
    age = _cache_age_seconds(path)
    return age is not None and age < DEFAULT_CACHE_TTL_SECONDS


def _game_props_cache_fresh(
    payload: dict[str, Any] | None,
    game_date: date,
    *,
    path: Path | None = None,
) -> bool:
    if not payload or not payload.get("props"):
        return False
    cached_day = str(payload.get("date") or "")
    if cached_day and cached_day != game_date.isoformat():
        return False
    ts = _parse_cached_timestamp(payload.get("fetched_at"))
    if ts is not None:
        return (_clock() - ts).total_seconds() < DEFAULT_CACHE_TTL_SECONDS
    if path is not None:
        age = _cache_age_seconds(path)
        return age is not None and age < DEFAULT_CACHE_TTL_SECONDS
    return True


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
        max_lines=None,
    )
    pick_lists = _game_pick_lists(enriched)
    return {
        **empty_payload,
        "props": enriched,
        **pick_lists,
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


def wipe_props_bet_cache() -> dict[str, Any]:
    """Delete all cached prop lines so the next scan must pull from the API."""
    removed = 0
    PROPS_DIR.mkdir(parents=True, exist_ok=True)
    for path in PROPS_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.name in (".cache_meta.json", ".gitkeep"):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Could not delete prop cache file %s: %s", path, exc)
    meta = {
        "generation": PROPS_CACHE_GENERATION,
        "wiped_at": _clock().isoformat(),
        "requires_refresh": True,
        "removed_files": removed,
    }
    _write_json(PROPS_CACHE_META_PATH, meta)
    logger.info(
        "Wiped props bet cache (generation=%s, removed=%s files)",
        PROPS_CACHE_GENERATION,
        removed,
    )
    return meta


def get_props_cache_meta() -> dict[str, Any]:
    meta = _load_json(PROPS_CACHE_META_PATH) or {}
    today = date.today()
    book = DEFAULT_DISPLAY_BOOKMAKER
    slate = _load_json(_slate_cache_path(today, book))
    stale_generation = meta.get("generation") != PROPS_CACHE_GENERATION
    stale_day = bool(meta.get("slate_date")) and meta.get("slate_date") != today.isoformat()
    stale_slate = slate is not None and not _slate_cache_fresh(slate, today)
    requires = bool(
        meta.get("requires_refresh") or stale_generation or stale_day or stale_slate
    )
    return {
        "generation": str(meta.get("generation") or ""),
        "expected_generation": PROPS_CACHE_GENERATION,
        "requires_refresh": requires,
        "wiped_at": meta.get("wiped_at"),
        "refreshed_at": meta.get("refreshed_at"),
        "removed_files": meta.get("removed_files"),
        "slate_date": meta.get("slate_date"),
        "slate_cached_at": slate.get("cached_at") if slate else None,
    }


def mark_props_cache_refreshed(game_date: date | None = None) -> None:
    meta = _load_json(PROPS_CACHE_META_PATH) or {}
    meta["generation"] = PROPS_CACHE_GENERATION
    meta["requires_refresh"] = False
    meta["refreshed_at"] = _clock().isoformat()
    meta["slate_date"] = (game_date or date.today()).isoformat()
    _write_json(PROPS_CACHE_META_PATH, meta)


def ensure_props_cache_generation() -> dict[str, Any] | None:
    """On deploy/restart, wipe stale prop caches when generation changes or day rolls over."""
    meta = _load_json(PROPS_CACHE_META_PATH) or {}
    today = date.today().isoformat()
    if meta.get("generation") != PROPS_CACHE_GENERATION:
        return wipe_props_bet_cache()
    if meta.get("slate_date") and meta.get("slate_date") != today:
        meta["requires_refresh"] = True
        _write_json(PROPS_CACHE_META_PATH, meta)
        logger.info(
            "Props cache marked for refresh (slate date %s -> %s)",
            meta.get("slate_date"),
            today,
        )
        return meta
    if meta.get("requires_refresh"):
        return meta
    return None


def _line_balance_score(over_odds: int, under_odds: int) -> float:
    """Lower score = prices closer to a typical main line (-110/-110)."""
    return abs(abs(over_odds) - 110) + abs(abs(under_odds) - 110)


def _collapse_to_primary_lines(
    store: dict[tuple[str, str, float, str], dict[str, Any]],
) -> dict[tuple[str, str, float, str], dict[str, Any]]:
    """Keep one real main line per player/market — both sides required."""
    out: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    main_groups: dict[tuple[str, str], list[tuple[tuple[str, str, float, str], dict[str, Any]]]] = (
        defaultdict(list)
    )
    for pk, row in store.items():
        player, market, _line, line_kind = pk
        if line_kind == "alternate":
            if row.get("over_odds") is not None and row.get("under_odds") is not None:
                out[pk] = {**row, "primary_line": False}
            continue
        if row.get("over_odds") is None or row.get("under_odds") is None:
            continue
        main_groups[(player, market)].append((pk, row))
    for _key, candidates in main_groups.items():
        best_pk, best_row = min(
            candidates,
            key=lambda item: (
                _line_balance_score(int(item[1]["over_odds"]), int(item[1]["under_odds"])),
                float(item[1]["line"]),
            ),
        )
        out[best_pk] = {**best_row, "primary_line": True}
    return out


def _published_lines_for_book(
    event: dict[str, Any],
    book: str,
    *,
    markets_requested: str = DEFAULT_MLB_PROP_MARKETS,
) -> list[dict[str, Any]]:
    return _filter_prop_markets(
        _parse_event_props(event, bookmaker_key=book),
        markets_requested=markets_requested,
    )


def _published_lines_index(
    event: dict[str, Any],
    book: str,
    *,
    markets_requested: str = DEFAULT_MLB_PROP_MARKETS,
) -> set[tuple[str, str, float, str]]:
    """Set of (player, market_type, line, side) the sportsbook actually posted."""
    index: set[tuple[str, str, float, str]] = set()
    for row in _published_lines_for_book(
        event, book, markets_requested=markets_requested
    ):
        if row.get("complete_market") is not True:
            continue
        if row.get("primary_line") is False:
            continue
        if row.get("line_kind") == "alternate":
            continue
        player = str(row.get("player") or "")
        market = str(row.get("market_type") or "")
        line = row.get("line")
        if not player or not market or line is None:
            continue
        line_f = float(line)
        if row.get("over_odds") is not None:
            index.add((player, market, line_f, "over"))
        if row.get("under_odds") is not None:
            index.add((player, market, line_f, "under"))
    return index


def _prop_published_key(prop: dict[str, Any]) -> tuple[str, str, float, str] | None:
    side = str(prop.get("recommended_side") or "").lower()
    if side not in ("over", "under"):
        return None
    player = str(prop.get("player") or "")
    market = str(prop.get("market_type") or "")
    line = prop.get("line")
    if not player or not market or line is None:
        return None
    return (player, market, float(line), side)


def _load_published_index(
    game_id: str,
    game_date: date,
    book: str,
) -> set[tuple[str, str, float, str]] | None:
    raw = _load_raw_event(str(game_id), game_date)
    if not raw or not raw.get("event"):
        return None
    return _published_lines_index(raw["event"], book)


def _prop_matches_published(
    prop: dict[str, Any],
    published: set[tuple[str, str, float, str]],
) -> bool:
    key = _prop_published_key(prop)
    if key is not None:
        return key in published
    player = str(prop.get("player") or "")
    market = str(prop.get("market_type") or "")
    line = prop.get("line")
    if not player or not market or line is None:
        return False
    line_f = float(line)
    return (
        (player, market, line_f, "over") in published
        and (player, market, line_f, "under") in published
    )


def _revalidate_pick_list(
    picks: list[dict[str, Any]],
    book: str,
    game_date: date,
) -> list[dict[str, Any]]:
    """Drop cached picks that are not in the raw sportsbook feed for this book."""
    index_cache: dict[str, set[tuple[str, str, float, str]] | None] = {}
    kept: list[dict[str, Any]] = []
    for prop in picks:
        gid = str(prop.get("game_id") or "")
        if not gid:
            continue
        if gid not in index_cache:
            index_cache[gid] = _load_published_index(gid, game_date, book)
        published = index_cache[gid]
        if published is not None and not _prop_matches_published(prop, published):
            continue
        kept.append(prop)
    return kept


def _apply_published_line_filter(payload: dict[str, Any]) -> dict[str, Any]:
    game_id = str(payload.get("game_id") or "")
    game_date_str = payload.get("date")
    book = _normalize_bookmaker(payload.get("bookmaker"))
    markets_requested = str(payload.get("markets_requested") or DEFAULT_MLB_PROP_MARKETS)
    if not game_id or not game_date_str:
        return payload
    try:
        game_date = date.fromisoformat(str(game_date_str))
    except ValueError:
        return payload
    published = _load_published_index(game_id, game_date, book)
    props: list[dict[str, Any]] = []
    for prop in payload.get("props") or []:
        if published is not None and not _prop_matches_published(prop, published):
            continue
        props.append(prop)
    return _trim_props_payload({**payload, "props": props}, markets_requested)


def _parse_event_props(
    event: dict[str, Any],
    bookmaker_key: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize Odds API event-odds to prop rows from real posted lines only.

    Per-book mode: exactly what that sportsbook returned (one-sided markets allowed).
    Consensus mode: median odds only when at least one book posted both Over and Under
    at the same player/market/line — never stitch Over from book A with Under from book B.
    """
    bookmaker_key = _normalize_bookmaker(
        bookmaker_key if bookmaker_key is not None else DEFAULT_PROP_BOOKMAKER
    )
    books = event.get("bookmakers") or []
    if bookmaker_key != DEFAULT_PROP_BOOKMAKER:
        books = [book for book in books if book.get("key") == bookmaker_key]
    else:
        books = [
            book
            for book in books
            if str(book.get("key") or "") in CONSENSUS_PROP_BOOKS
        ]

    per_book: dict[str, dict[tuple[str, str, float, str], dict[str, Any]]] = {}

    for book in books:
        bk = str(book.get("key") or "")
        if not bk:
            continue
        store = per_book.setdefault(bk, {})
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
                pk = (player, canonical_type, line, line_kind)
                row = store.setdefault(
                    pk,
                    {
                        "player": player,
                        "market_type": canonical_type,
                        "market_label": market_label(canonical_type),
                        "line": line,
                        "line_kind": line_kind,
                        "over_odds": None,
                        "under_odds": None,
                        "over_link": None,
                        "under_link": None,
                    },
                )
                if name == "over":
                    row["over_odds"] = american
                    if outcome.get("link"):
                        row["over_link"] = str(outcome["link"])
                else:
                    row["under_odds"] = american
                    if outcome.get("link"):
                        row["under_link"] = str(outcome["link"])

    for bk in list(per_book.keys()):
        per_book[bk] = _collapse_to_primary_lines(per_book[bk])

    rows: list[dict[str, Any]] = []

    if bookmaker_key != DEFAULT_PROP_BOOKMAKER:
        for bk, markets in per_book.items():
            for row in markets.values():
                if not row.get("over_odds") and not row.get("under_odds"):
                    continue
                complete = (
                    row.get("over_odds") is not None and row.get("under_odds") is not None
                )
                rows.append(
                    {
                        **row,
                        "complete_market": complete,
                        "offered_books": [bk],
                        "primary_line": row.get("primary_line", True),
                    }
                )
    else:
        by_line: dict[tuple[str, str, float, str], list[tuple[str, dict[str, Any]]]] = (
            defaultdict(list)
        )
        for bk, markets in per_book.items():
            for pk, row in markets.items():
                by_line[pk].append((bk, row))

        for pk, book_rows in by_line.items():
            complete_books = [
                (bk, row)
                for bk, row in book_rows
                if row.get("over_odds") is not None and row.get("under_odds") is not None
            ]
            if not complete_books:
                continue
            overs = [int(row["over_odds"]) for _, row in complete_books]
            unders = [int(row["under_odds"]) for _, row in complete_books]
            base = dict(complete_books[0][1])
            rows.append(
                {
                    **base,
                    "over_odds": _median_int(overs),
                    "under_odds": _median_int(unders),
                    "complete_market": True,
                    "primary_line": True,
                    "offered_books": [bk for bk, _ in complete_books],
                }
            )

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
    max_lines: int | None = MAX_PROP_LINES_TO_SCORE,
) -> list[dict[str, Any]]:
    if max_lines is not None and len(props) > max_lines:
        props = _sample_props_for_scoring(props, max_lines)

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
        merged = refresh_prop_line_strength({**row, **analysis})
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
    include_all_markets: bool = True,
    bookmaker: str | None = None,
) -> dict[str, Any] | None:
    """Fetch + score player props for one MLB game."""
    fetch_markets = markets or _markets_for_fetch(
        include_alternates=include_alternates,
        include_all_markets=include_all_markets,
    )
    game_date = game_date or date.today()
    book = _resolve_bookmaker(bookmaker)
    detail = get_mlb_game(game_id, game_date)
    if detail is None:
        if not refresh:
            cached = _load_cached_game_props(str(game_id), book, game_date=game_date)
            if cached:
                cached = _trim_props_payload(cached, fetch_markets)
                cached = _apply_published_line_filter(cached)
                cached = {
                    **cached,
                    "stale_cache": True,
                    "message": "Game not on today's schedule — showing cached props.",
                }
                return _mark_stale_props(cached)
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
        "very_strong_picks": [],
        "total_very_strong": 0,
        "fetched_at": None,
        "source": None,
        "status": "empty",
        "message": None,
        "markets_requested": fetch_markets,
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
    }

    if not refresh:
        cached = _load_cached_game_props(str(game_id), book, game_date=game_date)
        if cached and _markets_satisfy_request(
            str(cached.get("markets_requested") or ""),
            fetch_markets,
        ):
            cached = _trim_props_payload(cached, fetch_markets)
            cached = _apply_published_line_filter(cached)
            if age is not None and age >= DEFAULT_CACHE_TTL_SECONDS:
                cached = {**cached, "stale_cache": True}
            return _mark_stale_props(cached)
        raw = _load_raw_event(str(game_id), game_date)
        if raw and _raw_event_fresh(raw) and raw.get("event"):
            raw_markets = str(raw.get("markets") or "")
            if _markets_satisfy_request(raw_markets, fetch_markets):
                payload = _assemble_game_props_payload(
                    raw["event"],
                    game_id=str(game_id),
                    game_date=game_date,
                    game=game,
                    book=book,
                    fetch_markets=fetch_markets,
                    event_id=raw.get("event_id"),
                    source="raw_event_cache",
                    empty_payload=empty_payload,
                )
                payload = _apply_published_line_filter(payload)
                _write_json(cache_path, payload)
                return payload
        refresh = True

    event_id, event_err = _resolve_event_id(
        game_date, home_team, away_team, refresh=refresh
    )
    if not event_id:
        msg = _status_message(event_err)
        empty_payload["message"] = msg
        return empty_payload

    # Single-book fetch for a specific sportsbook; all books for consensus.
    api_books = book if book != DEFAULT_PROP_BOOKMAKER else None
    result = fetch_mlb_event_props_if_allowed(
        event_id,
        markets=fetch_markets,
        bookmakers=api_books,
    )
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
    payload = _apply_published_line_filter(payload)
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


EXPORT_BOOK_HINTS: dict[str, str] = {
    "draftkings": "DraftKings: Parlay tab → search each player → add the matching selection.",
    "fanduel": "FanDuel: Same Game Parlay+ or parlay hub → search player → add each leg.",
    "betmgm": "BetMGM: One Game Parlay / parlay builder → find each prop below.",
    "betrivers": "BetRivers: Parlay builder → search player props and add each leg.",
    "williamhill_us": "Caesars: Build parlay → search player → add matching props.",
    "bovada": "Bovada: Parlay → player props → add each selection below.",
    "betonlineag": "BetOnline: Parlay → find each player prop in the list below.",
    "espnbet": "theScore Bet: Parlay → search player → add each leg.",
    "fanatics": "Fanatics Sportsbook: Parlay → search player → add each prop.",
}


def _normalize_player_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _prop_match_key(row: dict[str, Any]) -> tuple[str, str, float] | None:
    player = _normalize_player_name(row.get("player"))
    market = row.get("market_type")
    if not player or not market:
        return None
    try:
        line = float(row["line"])
    except (TypeError, ValueError, KeyError):
        return None
    return (player, str(market), line)


def _props_have_deeplinks(props: list[dict[str, Any]]) -> bool:
    return any(p.get("over_link") or p.get("under_link") for p in props)


def _merge_deeplinks_into_props(
    props: list[dict[str, Any]],
    link_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy over_link/under_link from parsed event rows onto cached enriched props."""
    link_map: dict[tuple[str, str, float], dict[str, Any]] = {}
    for row in link_rows:
        key = _prop_match_key(row)
        if key and (row.get("over_link") or row.get("under_link")):
            link_map[key] = row
    if not link_map:
        return props
    merged: list[dict[str, Any]] = []
    for prop in props:
        key = _prop_match_key(prop)
        links = link_map.get(key) if key else None
        if links:
            patch = {
                k: links[k]
                for k in ("over_link", "under_link")
                if links.get(k)
            }
            merged.append({**prop, **patch})
        else:
            merged.append(prop)
    return merged


def _persist_prop_links_in_cache(
    game_id: str,
    bookmaker: str,
    props: list[dict[str, Any]],
) -> None:
    """Write deeplink fields back into the on-disk game props cache."""
    cached = _load_cached_game_props(game_id, bookmaker)
    if not cached:
        return
    prop_map = {
        key: p for p in props if (key := _prop_match_key(p)) is not None
    }
    updated_props: list[dict[str, Any]] = []
    for prop in cached.get("props") or []:
        key = _prop_match_key(prop)
        links = prop_map.get(key) if key else None
        if links and (links.get("over_link") or links.get("under_link")):
            patch = {
                k: links[k]
                for k in ("over_link", "under_link")
                if links.get(k)
            }
            updated_props.append({**prop, **patch})
        else:
            updated_props.append(prop)
    _write_json(_props_cache_path(game_id, bookmaker), {**cached, "props": updated_props})


def _game_date_candidates(
    game_id: str,
    *,
    leg: dict[str, Any] | None = None,
    cached_payload: dict[str, Any] | None = None,
) -> list[date]:
    """Dates to try when resolving props for a game (slip legs may span prior slates)."""
    seen: set[str] = set()
    out: list[date] = []

    def add(raw: date | str | None) -> None:
        if raw is None:
            return
        if isinstance(raw, date):
            d = raw
        else:
            try:
                d = date.fromisoformat(str(raw)[:10])
            except ValueError:
                return
        key = d.isoformat()
        if key not in seen:
            seen.add(key)
            out.append(d)

    if leg:
        add(leg.get("game_date"))
    if cached_payload:
        add(cached_payload.get("date"))
    raw_dir = _raw_events_dir()
    if raw_dir.is_dir():
        for path in sorted(raw_dir.glob(f"{game_id}.*.json"), reverse=True):
            suffix = path.stem.rsplit(".", 1)[-1]
            add(suffix)
    add(date.today())
    add(date.today() - timedelta(days=1))
    return out


def _find_raw_event_any_date(game_id: str) -> dict[str, Any] | None:
    """Load the newest raw Odds API event snapshot for a game id."""
    raw_dir = _raw_events_dir()
    if not raw_dir.is_dir():
        return None
    for path in sorted(raw_dir.glob(f"{game_id}.*.json"), reverse=True):
        payload = _load_json(path)
        if payload and payload.get("event"):
            return payload
    return None


def _load_game_props_for_export(
    game_id: str,
    bookmaker: str,
    *,
    refresh_links: bool = False,
    leg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load props for slip export, backfilling deeplinks from raw cache or live refresh."""
    book = _resolve_bookmaker(bookmaker)
    try_date = None
    if leg and leg.get("game_date"):
        try:
            try_date = date.fromisoformat(str(leg["game_date"])[:10])
        except ValueError:
            try_date = None
    cached_payload = _load_cached_game_props(
        str(game_id), book, game_date=try_date
    )
    props = (cached_payload or {}).get("props") or []
    resolved_date: date | None = None

    if not props:
        for try_date in _game_date_candidates(
            str(game_id), leg=leg, cached_payload=cached_payload
        ):
            payload = build_game_props(
                str(game_id),
                game_date=try_date,
                bookmaker=book,
                refresh=False,
            )
            if payload and payload.get("props"):
                props = payload["props"]
                resolved_date = try_date
                cached_payload = payload
                break
    elif cached_payload and cached_payload.get("date"):
        try:
            resolved_date = date.fromisoformat(str(cached_payload["date"])[:10])
        except ValueError:
            resolved_date = None

    if _props_have_deeplinks(props):
        return props

    raw = _find_raw_event_any_date(str(game_id))
    if raw is None and resolved_date is not None:
        raw = _load_raw_event(str(game_id), resolved_date)
    if raw and raw.get("event"):
        link_rows = _parse_event_props(raw["event"], bookmaker_key=book)
        if _props_have_deeplinks(link_rows):
            if props:
                props = _merge_deeplinks_into_props(props, link_rows)
                _persist_prop_links_in_cache(str(game_id), book, props)
            else:
                props = link_rows
            return props

    if refresh_links:
        for try_date in _game_date_candidates(
            str(game_id), leg=leg, cached_payload=cached_payload
        ):
            if get_mlb_game(str(game_id), try_date) is None:
                continue
            payload = build_game_props(
                str(game_id),
                game_date=try_date,
                bookmaker=book,
                refresh=True,
            )
            refreshed = (payload or {}).get("props") or []
            if refreshed:
                return refreshed

    return props


def _prop_side_link(prop: dict[str, Any], side: str) -> str | None:
    key = "over_link" if side == "over" else "under_link"
    link = prop.get(key) or (prop.get("deeplink") if side == prop.get("recommended_side") else None)
    return str(link) if link else None


def _format_export_american(odds: int | None) -> str:
    if odds is None:
        return "—"
    return f"+{odds}" if odds > 0 else str(odds)


def _find_prop_for_slip_leg(
    props: list[dict[str, Any]],
    leg: dict[str, Any],
) -> dict[str, Any] | None:
    player = _normalize_player_name(leg.get("player"))
    market = leg.get("market_type")
    side = str(leg.get("side") or "over").lower()
    if not player or not market:
        return None
    try:
        target_line = float(leg["line"]) if leg.get("line") is not None else None
    except (TypeError, ValueError):
        target_line = None

    odds_key = "over_odds" if side == "over" else "under_odds"
    for prop in props:
        if _normalize_player_name(prop.get("player")) != player:
            continue
        if prop.get("market_type") != market:
            continue
        if target_line is not None and prop.get("line") is not None:
            try:
                if abs(float(prop["line"]) - target_line) > 0.001:
                    continue
            except (TypeError, ValueError):
                continue
        if prop.get(odds_key) is None:
            continue
        return prop
    return None


def _slip_leg_export_line(leg: dict[str, Any]) -> str:
    side = "Under" if leg.get("side") == "under" else "Over"
    market = leg.get("market_label") or leg.get("market_type") or "Prop"
    line = leg.get("line", "")
    player = leg.get("player", "")
    text = f"{player} {side} {line} {market}".strip()
    odds = leg.get("american_odds")
    if odds is not None:
        text += f" ({_format_export_american(int(odds))})"
    if leg.get("matchup"):
        text += f" · {leg['matchup']}"
    if not leg.get("available_at_book", True):
        text += " [NOT AT BOOK — find similar line manually]"
    elif leg.get("line_changed"):
        text += f" [was {leg.get('original_line')} on your slip]"
    return text


def format_slip_export_text(
    legs: list[dict[str, Any]],
    *,
    bookmaker: str,
    bookmaker_label: str,
    parlay: dict[str, Any] | None = None,
) -> str:
    """Plain-text slip formatted for a specific sportsbook."""
    parlay = parlay or evaluate_prop_parlay(legs)
    available = [leg for leg in legs if leg.get("available_at_book", True)]

    lines = [
        f"{len(legs)}-Leg Prop Parlay — {bookmaker_label}",
        "NTG Sports",
        "",
    ]
    for i, leg in enumerate(legs, 1):
        lines.append(f"{i}. {_slip_leg_export_line(leg)}")

    lines.append("")
    american = parlay.get("american_payout")
    if american is not None:
        lines.append(
            f"Combined odds at {bookmaker_label}: {_format_export_american(int(american))} (approx)"
        )
    profit = parlay.get("profit_on_10")
    if profit is not None:
        lines.append(f"$10 stake → ${profit:.2f} profit if all legs hit")

    missing = len(legs) - len(available)
    if missing:
        lines.append("")
        lines.append(
            f"Warning: {missing} leg(s) not found at {bookmaker_label} — verify before betting."
        )

    hint = EXPORT_BOOK_HINTS.get(
        bookmaker,
        f"Open {bookmaker_label} and add each leg to your parlay.",
    )
    lines.extend(["", hint])
    return "\n".join(lines)


def export_slip_for_bookmaker(
    legs: list[dict[str, Any]],
    bookmaker: str | None,
    *,
    refresh_links: bool = True,
) -> dict[str, Any]:
    """Reprice slip legs at the chosen sportsbook and build export text."""
    book = _resolve_bookmaker(bookmaker)
    if book == DEFAULT_PROP_BOOKMAKER:
        book = DEFAULT_DISPLAY_BOOKMAKER
    book_label = _bookmaker_label(book)

    game_props_cache: dict[str, list[dict[str, Any]]] = {}
    repriced: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for leg in legs:
        game_id = str(leg.get("game_id") or "")
        side = str(leg.get("side") or "over").lower()
        if not game_id:
            row = {
                **leg,
                "bookmaker": book,
                "bookmaker_label": book_label,
                "available_at_book": False,
                "export_note": "Missing game id",
            }
            repriced.append(row)
            missing.append(row)
            continue

        if game_id not in game_props_cache:
            game_props_cache[game_id] = _load_game_props_for_export(
                game_id,
                book,
                refresh_links=refresh_links,
                leg=leg,
            )

        match = _find_prop_for_slip_leg(game_props_cache[game_id], leg)
        if not match:
            leg_deeplink = leg.get("deeplink")
            row = {
                **leg,
                "bookmaker": book,
                "bookmaker_label": book_label,
                "available_at_book": False,
                "export_note": f"Not offered at {book_label}",
                "deeplink": leg_deeplink if leg_deeplink else None,
            }
            repriced.append(row)
            missing.append(row)
            continue

        odds_key = "over_odds" if side == "over" else "under_odds"
        american = match.get(odds_key)
        line = match.get("line", leg.get("line"))
        original_line = leg.get("line")
        line_changed = (
            original_line is not None
            and line is not None
            and abs(float(line) - float(original_line)) > 0.001
        )
        repriced.append(
            {
                **leg,
                "line": line,
                "american_odds": american,
                "bookmaker": book,
                "bookmaker_label": book_label,
                "available_at_book": american is not None,
                "line_changed": line_changed,
                "original_line": original_line if line_changed else None,
                "deeplink": _prop_side_link(match, side) or leg.get("deeplink"),
            }
        )

    deeplink_legs = [leg for leg in repriced if leg.get("deeplink")]
    parlay = evaluate_prop_parlay([leg for leg in repriced if leg.get("available_at_book")])
    export_text = format_slip_export_text(
        repriced,
        bookmaker=book,
        bookmaker_label=book_label,
        parlay=parlay,
    )
    return {
        "bookmaker": book,
        "bookmaker_label": book_label,
        "legs": repriced,
        "missing_count": len(missing),
        "missing": missing,
        "parlay": parlay,
        "export_text": export_text,
        "deeplink_count": len(deeplink_legs),
        "can_open_in_book": bool(deeplink_legs),
        "open_strategy": (
            "single" if len(deeplink_legs) == 1 else "multi" if deeplink_legs else "none"
        ),
    }


def _load_cached_game_props(
    game_id: str,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
    *,
    game_date: date | None = None,
) -> dict[str, Any] | None:
    """Return on-disk game props when present, fresh, and for the requested slate date."""
    book = _normalize_bookmaker(bookmaker)
    path = _props_cache_path(game_id, book)
    payload = _load_json(path)
    if payload and payload.get("props"):
        if game_date is not None and not _game_props_cache_fresh(
            payload, game_date, path=path
        ):
            return None
        return payload
    if book == DEFAULT_PROP_BOOKMAKER:
        legacy_path = PROPS_DIR / f"{game_id}.json"
        legacy = _load_json(legacy_path)
        if legacy and legacy.get("props"):
            if game_date is not None and not _game_props_cache_fresh(
                legacy, game_date, path=legacy_path
            ):
                return None
            return legacy
    return None


def _load_best_slate_props(
    game_date: date,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    """Today's slate aggregate for the requested date and book (no cross-day fallback)."""
    book = _normalize_bookmaker(bookmaker)
    cached = _load_json(_slate_cache_path(game_date, book))
    if cached and cached.get("all_props") is not None:
        if not _slate_cache_fresh(cached, game_date):
            return [], "stale_slate_cache", cached
        return cached["all_props"] or [], "slate_cache", cached
    if book == DEFAULT_PROP_BOOKMAKER:
        legacy = _load_json(PROPS_DIR / f"slate_{game_date.isoformat()}.json")
        if legacy and legacy.get("all_props") is not None:
            if not _slate_cache_fresh(legacy, game_date):
                return [], "stale_slate_cache", legacy
            return legacy["all_props"] or [], "slate_cache", legacy
    return [], "none", None


def _aggregate_repo_game_props(
    game_date: date,
    bookmaker: str = DEFAULT_PROP_BOOKMAKER,
) -> list[dict[str, Any]]:
    """Merge bettable props from per-game cache files for the requested date."""
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
        if payload.get("date") and payload.get("date") != game_date.isoformat():
            continue
        age = _cache_age_seconds(path)
        if age is not None and age >= DEFAULT_CACHE_TTL_SECONDS:
            payload = {**payload, "stale_cache": True}
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
    very_strong_props: list[dict[str, Any]] | None = None,
    top_props: list[dict[str, Any]] | None = None,
    log_tracker: bool = True,
) -> dict[str, Any]:
    from app.odds.live_odds import live_odds_enabled

    book = _normalize_bookmaker(bookmaker)
    if very_strong_props is None or top_props is None:
        very_strong_props, top_props = _split_slate_props(picks)
    out: dict[str, Any] = {
        "date": game_date.isoformat(),
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
        "games_on_slate": games_on_slate,
        "games_scanned": games_scanned,
        "games_fetched": games_fetched,
        "fetch_cap_hit": fetch_cap_hit,
        "total_actionable": len(picks),
        "total_very_strong": len(very_strong_props),
        "very_strong_props": very_strong_props[:limit],
        "top_props": top_props[:limit],
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
    if log_tracker and picks and live_odds_enabled():
        from app.services.prop_pick_tracker import log_offered_props

        logged = log_offered_props(
            picks,
            game_date.isoformat(),
            source=f"daily_{source}",
        )
        out["props_logged_count"] = len(logged)
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
    book = _resolve_bookmaker(bookmaker)
    if refresh:
        scan = True

    if not scan and not refresh:
        picks, source, meta = _load_best_slate_props(game_date, book)
        if picks and source != "stale_slate_cache":
            picks = _normalize_scored_props(
                _filter_prop_markets(picks, markets_requested=DEFAULT_MLB_PROP_MARKETS)
            )
            picks = [p for p in picks if prop_is_bettable(p)]
            picks = _revalidate_pick_list(picks, book, game_date)
            picks.sort(key=prop_rank_key)
            very_strong, regular = _split_slate_props(picks)
            return _daily_props_payload(
                game_date=game_date,
                limit=limit,
                picks=picks,
                very_strong_props=very_strong,
                top_props=regular,
                source=source,
                games_on_slate=meta.get("games_on_slate", 0) if meta else 0,
                games_scanned=meta.get("games_scanned", 0) if meta else 0,
                cached_at=meta.get("cached_at") if meta else None,
                bookmaker=book,
            )
        if source == "stale_slate_cache":
            scan = True

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
        cached_payload = None if refresh else _load_cached_game_props(
            gid, book, game_date=game_date
        )
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

    if not picks and not refresh:
        picks = _aggregate_repo_game_props(game_date, book)
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
    picks = _revalidate_pick_list(picks, book, game_date)
    if picks and scan:
        very_strong, regular = _split_slate_props(picks)
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
                "very_strong_props": very_strong,
                "top_props": regular,
                "total_actionable": len(picks),
                "total_very_strong": len(very_strong),
            },
        )

    if refresh and games_fetched > 0:
        mark_props_cache_refreshed(game_date)

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
    game_date = payload.get("date")
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
                prop,
                game_id=game_id,
                matchup=matchup,
                bookmaker=bookmaker,
                game_date=game_date,
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
    very_strong_only: bool = False,
) -> bool:
    if very_strong_only and not is_very_strong_prop(prop):
        return False
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
    very_strong_only: bool = False,
    limit: int = 100,
    scan: bool = False,
    refresh: bool = False,
    include_alternates: bool = False,
) -> dict[str, Any]:
    """Filter scored props across the slate for the props explorer page."""
    game_date = game_date or date.today()
    book = _resolve_bookmaker(bookmaker)
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

    markets = _markets_for_fetch(include_alternates=include_alternates)
    schedule = get_mlb_schedule(game_date)
    games = schedule.get("games") or []
    games_on_slate = len([g for g in games if g.get("game_id")])
    fetch_limit = _max_slate_prop_fetch(games_on_slate)
    games_fetched = 0
    games_scanned = 0
    games_with_props = 0
    pool_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    def _merge_payload(payload: dict[str, Any] | None, gid: str) -> None:
        nonlocal games_scanned, games_with_props
        if not payload:
            return
        payload = _trim_props_payload(payload, markets)
        props = payload.get("props") or []
        if not props:
            return
        games_scanned += 1
        games_with_props += 1
        for row in _collect_scored_props_from_payload(payload, gid):
            key = (
                gid,
                row.get("player"),
                row.get("market_type"),
                row.get("line"),
                row.get("recommended_side"),
            )
            pool_by_key[key] = row

    for game in games:
        gid = str(game.get("game_id") or "")
        if not gid:
            continue
        payload = None if refresh else _load_cached_game_props(
            gid, book, game_date=game_date
        )
        if payload and payload.get("props"):
            _merge_payload(payload, gid)
            continue

        if scan and games_fetched < fetch_limit:
            built = build_game_props(
                gid,
                game_date=game_date,
                refresh=True,
                bookmaker=book,
                include_alternates=include_alternates,
            )
            if built:
                games_fetched += 1
                _merge_payload(built, gid)
            continue

        built = build_game_props(
            gid,
            game_date=game_date,
            refresh=False,
            bookmaker=book,
            include_alternates=include_alternates,
        )
        if built and built.get("props"):
            _merge_payload(built, gid)
            continue

        raw = _load_raw_event(gid, game_date)
        if raw and _raw_event_fresh(raw) and raw.get("event"):
            built = build_game_props(
                gid,
                game_date=game_date,
                refresh=False,
                bookmaker=book,
                include_alternates=include_alternates,
            )
            _merge_payload(built, gid)

    pool = list(pool_by_key.values())
    if not pool:
        pool = _aggregate_repo_game_props(game_date, book)
        for prop in pool:
            gid = str(prop.get("game_id") or "")
            if gid:
                key = (
                    gid,
                    prop.get("player"),
                    prop.get("market_type"),
                    prop.get("line"),
                    prop.get("recommended_side"),
                )
                pool_by_key[key] = prop
        pool = list(pool_by_key.values())
    if not pool:
        picks, _, _ = _load_best_slate_props(game_date, book)
        pool = picks or (
            (base.get("very_strong_props") or []) + (base.get("top_props") or [])
        )

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
            very_strong_only=very_strong_only,
        )
    ]
    filtered.sort(key=prop_rank_key)

    if pool and not games_with_props:
        games_with_props = len(
            {str(p.get("game_id")) for p in pool if p.get("game_id")}
        )

    hint = base.get("hint")
    if line_kind == "alternate" and not include_alternates:
        hint = "Alternate lines require a refresh with alternates enabled."
    elif games_on_slate and games_with_props < games_on_slate:
        coverage = f"{games_with_props}/{games_on_slate} games"
        hint = (
            f"Props loaded from {coverage}. "
            "Use Refresh lines to scan games missing cached props."
        )

    very_strong_matched = sum(1 for prop in filtered if is_very_strong_prop(prop))

    return {
        "date": game_date.isoformat(),
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
        "market_types": list_prop_market_types(),
        "total_matched": len(filtered),
        "total_very_strong": very_strong_matched,
        "total_in_pool": len(pool),
        "games_on_slate": games_on_slate,
        "games_scanned": games_scanned,
        "games_with_props": games_with_props,
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
            "very_strong_only": very_strong_only,
            "include_alternates": include_alternates,
        },
    }
