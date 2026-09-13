"""NFL player props: Odds API lines + NFL-specific projections.

MLB scoring/cache is untouched. This module writes to
data/processed/props_repository/nfl/ only.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.nfl_odds_repository import load_date as load_nfl_odds_date
from app.odds.nfl_team_aliases import normalize_nfl_team
from app.odds.odds_repository import (
    _median_int,
    fetch_nfl_event_props_if_allowed,
    fetch_nfl_events_if_allowed,
)
from app.odds.team_aliases import is_valid_american_odds
from app.odds.the_odds_api import (
    ALTERNATE_NFL_PROP_MARKETS,
    DEFAULT_NFL_PROP_MARKETS,
    SLATE_NFL_PROP_MARKETS,
    SPORT_NFL,
)
from app.services.nfl_player_stats import (
    game_environment,
    nfl_game_log_entries,
    opponent_defense_profile,
    player_injury_note,
    resolve_nfl_player,
    similar_opponent_abbrs,
)
from app.services.prop_engine.nfl_context import (
    LINE_STRENGTH_LABELS,
    cash_confidence,
    defense_label,
    defense_market_multiplier,
    hit_rates_from_log,
    kickoff_market_multiplier,
    kickoff_profile,
    recommended_hit_rates,
    weather_market_multiplier,
    weather_profile,
)
from app.services.prop_engine.nfl_markets import (
    MARKET_STAT,
    canonical_market_type,
    market_label,
    position_group,
)
from app.services.prop_engine.nfl_projections import (
    YES_NO_LINE,
    build_nfl_projection,
    market_fair_probs,
    nfl_side_probabilities,
    score_nfl_prop,
)
from app.services.prop_books import (
    CONSENSUS_PROP_BOOKS,
    DEFAULT_DISPLAY_BOOKMAKER,
    DEFAULT_PROP_BOOKMAKER,
    bookmaker_label,
    list_static_prop_bookmakers,
    normalize_prop_bookmaker,
)
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

NFL_PROPS_DIR = PROJECT_ROOT / "data" / "processed" / "props_repository" / "nfl"
EVENTS_CACHE = NFL_PROPS_DIR / "events"
DEFAULT_CACHE_TTL_SECONDS = int(os.getenv("PROPS_CACHE_TTL_SECONDS", "7200"))
NFL_PROP_MODEL_VERSION = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _normalize_bookmaker(raw: str | None) -> str:
    return normalize_prop_bookmaker(raw)


def _bookmaker_label(book: str) -> str:
    return bookmaker_label(book)


def _cache_path(game_date: date, game_id: str, book: str) -> Path:
    return NFL_PROPS_DIR / game_date.isoformat() / f"{game_id}.{book}.json"


def _events_path(game_date: date) -> Path:
    return EVENTS_CACHE / f"{game_date.isoformat()}.json"


def _game_started(game: dict[str, Any]) -> bool:
    status = str(game.get("status") or "").lower()
    return status in {"in", "live", "final", "post", "in progress", "halftime"}


def _team_abbr(game: dict[str, Any], side: str) -> str:
    explicit = game.get(f"{side}_team_abbr")
    if explicit:
        return normalize_nfl_team(explicit)
    return normalize_nfl_team(game.get(f"{side}_team") or "")


def parse_nfl_event_props(
    event: dict[str, Any],
    bookmaker_key: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize Odds API NFL event-odds. Real posted lines only."""
    bookmaker_key = _normalize_bookmaker(
        bookmaker_key if bookmaker_key is not None else DEFAULT_PROP_BOOKMAKER
    )
    books = event.get("bookmakers") or []
    if bookmaker_key != DEFAULT_PROP_BOOKMAKER:
        books = [book for book in books if book.get("key") == bookmaker_key]
    else:
        books = [book for book in books if str(book.get("key") or "") in CONSENSUS_PROP_BOOKS]

    per_book: dict[str, dict[tuple[str, str, float, str], dict[str, Any]]] = {}
    for book in books:
        bk = str(book.get("key") or "")
        if not bk:
            continue
        store = per_book.setdefault(bk, {})
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or "")
            if not market_key.startswith("player_"):
                continue
            canonical_type, line_kind = canonical_market_type(market_key)
            for outcome in market.get("outcomes") or []:
                name = str(outcome.get("name") or "").strip().lower()
                player = str(outcome.get("description") or "").strip()
                price = outcome.get("price")
                if not player or price is None:
                    continue
                try:
                    american = int(price)
                except (TypeError, ValueError):
                    continue
                if not is_valid_american_odds(american):
                    continue
                side = None
                line = outcome.get("point")
                if name in ("over", "under"):
                    side = name
                    if line is None:
                        continue
                    try:
                        line = float(line)
                    except (TypeError, ValueError):
                        continue
                elif name in ("yes", "no") and canonical_type == "player_anytime_td":
                    side = "over" if name == "yes" else "under"
                    line = YES_NO_LINE
                else:
                    continue
                pk = (player, canonical_type, float(line), line_kind)
                row = store.setdefault(
                    pk,
                    {
                        "player": player,
                        "market_type": canonical_type,
                        "market_label": market_label(canonical_type),
                        "line": float(line),
                        "line_kind": line_kind,
                        "over_odds": None,
                        "under_odds": None,
                    },
                )
                if side == "over":
                    row["over_odds"] = american
                    if outcome.get("link"):
                        row["over_link"] = str(outcome["link"])
                else:
                    row["under_odds"] = american
                    if outcome.get("link"):
                        row["under_link"] = str(outcome["link"])

    rows: list[dict[str, Any]] = []
    if bookmaker_key != DEFAULT_PROP_BOOKMAKER:
        for bk, markets in per_book.items():
            for row in markets.values():
                if row.get("over_odds") is None and row.get("under_odds") is None:
                    continue
                complete = row.get("over_odds") is not None and row.get("under_odds") is not None
                rows.append({**row, "complete_market": complete, "offered_books": [bk]})
    else:
        by_line: dict[tuple[str, str, float, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for bk, markets in per_book.items():
            for pk, row in markets.items():
                by_line[pk].append((bk, row))
        for _pk, book_rows in by_line.items():
            complete_books = [
                (bk, row)
                for bk, row in book_rows
                if row.get("over_odds") is not None and row.get("under_odds") is not None
            ]
            if not complete_books:
                continue
            overs = [int(row["over_odds"]) for _, row in complete_books]
            unders = [int(row["under_odds"]) for _, row in complete_books]
            over_median = _median_int(overs)
            under_median = _median_int(unders)
            if over_median is None or under_median is None:
                continue
            base = dict(complete_books[0][1])
            rows.append(
                {
                    **base,
                    "over_odds": over_median,
                    "under_odds": under_median,
                    "complete_market": True,
                    "offered_books": [bk for bk, _ in complete_books],
                }
            )
    rows.sort(key=lambda r: (r["market_type"], r["player"], r["line"]))
    return rows


def _match_odds_event(
    events: list[dict[str, Any]],
    home_abbr: str,
    away_abbr: str,
) -> dict[str, Any] | None:
    home = normalize_nfl_team(home_abbr)
    away = normalize_nfl_team(away_abbr)
    for event in events:
        ev_home = normalize_nfl_team(event.get("home_team") or "")
        ev_away = normalize_nfl_team(event.get("away_team") or "")
        if ev_home == home and ev_away == away:
            return event
    return None


def _load_events(game_date: date, *, refresh: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    path = _events_path(game_date)
    cached = None if refresh else _load_json(path)
    if cached and cached.get("events"):
        fetched = cached.get("fetched_at")
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
            ).total_seconds()
        except (TypeError, ValueError):
            age = DEFAULT_CACHE_TTL_SECONDS + 1
        if age < DEFAULT_CACHE_TTL_SECONDS:
            return list(cached["events"]), None
    result = fetch_nfl_events_if_allowed()
    if result.denied:
        return list((cached or {}).get("events") or []), result.denied_reason
    if result.error:
        return list((cached or {}).get("events") or []), result.error
    events = result.events or []
    _write_json(
        path,
        {"date": game_date.isoformat(), "fetched_at": _utc_now(), "events": events, "source": result.source},
    )
    return events, None


def _odds_environment(game: dict[str, Any], game_date: date) -> dict[str, Any]:
    home = _team_abbr(game, "home")
    away = _team_abbr(game, "away")
    spread = game.get("espn_spread")
    total = game.get("espn_ou")
    repo = load_nfl_odds_date(game_date) or {}
    for row in repo.get("games") or []:
        if normalize_nfl_team(row.get("home_team")) == home and normalize_nfl_team(row.get("away_team")) == away:
            spread = row.get("spread_home") if row.get("spread_home") is not None else spread
            total = row.get("total") if row.get("total") is not None else total
            break
    try:
        spread_f = float(spread) if spread is not None else None
    except (TypeError, ValueError):
        spread_f = None
    try:
        total_f = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_f = None
    return {"spread_home": spread_f, "total": total_f, "home": home, "away": away}


def _player_team(player: str, home: str, away: str) -> tuple[str, str, bool]:
    resolved = resolve_nfl_player(player, home) or resolve_nfl_player(player, away)
    if resolved:
        # Roster lookup is team-scoped first; fall back to name match on either side.
        home_hit = resolve_nfl_player(player, home)
        if home_hit:
            return home, away, True
        away_hit = resolve_nfl_player(player, away)
        if away_hit:
            return away, home, False
    return home, away, True


def score_nfl_prop_row(
    row: dict[str, Any],
    *,
    game: dict[str, Any],
    game_date: date,
    env: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate Over and Under; return one recommended-side row (plus analysis)."""
    home = env["home"]
    away = env["away"]
    player = str(row.get("player") or "")
    team, opponent, is_home = _player_team(player, home, away)
    resolved = resolve_nfl_player(player, team)
    position = position_group((resolved or {}).get("position"))
    athlete_id = (resolved or {}).get("athlete_id")
    injury = player_injury_note(player, team)
    context = game_environment(
        team_abbr=team,
        opponent_abbr=opponent,
        home=is_home,
        spread_home=env.get("spread_home"),
        total=env.get("total"),
    )
    weather = weather_profile(game)
    kickoff = kickoff_profile(game.get("start_time_utc"))
    try:
        defense = opponent_defense_profile(opponent)
    except Exception:
        defense = None
    market_type = str(row.get("market_type") or "")
    def_mult = defense_market_multiplier(market_type, defense)
    wx_mult = weather_market_multiplier(market_type, weather)
    ko_mult = kickoff_market_multiplier(market_type, kickoff)
    stat_key = MARKET_STAT.get(market_type)
    entries: list[dict[str, Any]] = []
    if athlete_id and stat_key:
        entries = nfl_game_log_entries(athlete_id, stat_key, before=game_date)
    values = [float(row["stat_value"]) for row in entries]
    try:
        line = float(row["line"])
    except (TypeError, ValueError, KeyError):
        return []
    rates = hit_rates_from_log(
        entries,
        line,
        slate_date=game_date,
        opponent=opponent,
        similar_opponents=similar_opponent_abbrs(opponent),
        market_stat=stat_key,
    )
    projection = build_nfl_projection(
        values,
        market_type=market_type,
        team_spread=context.get("team_spread"),
        team_implied_total=context.get("team_implied_total"),
        injury_note=injury,
        defense_multiplier=def_mult,
        weather_multiplier=wx_mult,
        kickoff_multiplier=ko_mult,
    )
    fair = market_fair_probs(row.get("over_odds"), row.get("under_odds"))
    model_probs = {"model_probability_over": None, "model_probability_under": None}
    if projection.get("model_projection") is not None:
        model_probs = nfl_side_probabilities(
            line,
            market_type=market_type,
            projection=float(projection["model_projection"]),
            std_dev=projection.get("std_dev"),
            empirical_values=values or None,
        )

    sides: list[dict[str, Any]] = []
    for side, odds_key, model_key, mkt_key in (
        ("over", "over_odds", "model_probability_over", "market_probability_over"),
        ("under", "under_odds", "model_probability_under", "market_probability_under"),
    ):
        odds = row.get(odds_key)
        if odds is None:
            continue
        model_p = model_probs.get(model_key)
        market_p = fair.get(mkt_key)
        edge = None
        if model_p is not None and market_p is not None:
            edge = round(float(model_p) - float(market_p), 4)
        scored = score_nfl_prop(
            edge=edge,
            sample_games=int(projection.get("sample_games") or 0),
            role_shift=projection.get("role_shift"),
            projection_confidence=str(projection.get("projection_confidence") or "low"),
            injury_note=injury,
        )
        risk_flags = []
        if injury:
            status = injury.split(" — ", 1)[0].upper()
            if "QUESTIONABLE" in status:
                risk_flags.append("QUESTIONABLE")
            elif "OUT" in status or "DOUBTFUL" in status:
                risk_flags.append("RETURNING FROM INJURY" if "OUT" not in status else "OUT")
            else:
                risk_flags.append(status)
        if (projection.get("role_shift") or 0) >= 0.35:
            risk_flags.append("ROLE CHANGE")
        if int(rates.get("sample_games_season") or 0) < 3 and int(projection.get("sample_games") or 0) >= 5:
            risk_flags.append("PRIOR SEASON FORM")
        elif int(projection.get("sample_games") or 0) < 3:
            risk_flags.append("LIMITED SAMPLE")
        if weather.get("risk") in ("moderate", "high"):
            risk_flags.append("WEATHER")
        rec_hits = recommended_hit_rates(rates, side)
        l5_n = int(rates.get("sample_games_l5") or 0)
        l10_n = int(rates.get("sample_games_l10") or 0)
        l5_hits = rec_hits.get("l5_hits")
        l10_hits = rec_hits.get("l10_hits")
        conf = cash_confidence(
            model_p=model_p,
            hit_l5=rec_hits.get("l5"),
            hit_l10=rec_hits.get("l10"),
            hit_vs_opp=rec_hits.get("vs_opp"),
            hit_vs_similar=rec_hits.get("vs_similar"),
            sample_games=int(projection.get("sample_games") or 0),
            injury_note=injury,
            weather_risk=str(weather.get("risk") or "none"),
            projection_confidence=str(projection.get("projection_confidence") or "low"),
        )
        matchup_note = defense_label(defense, market_type)
        factors = []
        if projection.get("l3_avg") is not None:
            factors.append(f"L3 avg {projection['l3_avg']}")
        if projection.get("season_avg") is not None:
            factors.append(f"Season avg {projection['season_avg']}")
        if rec_hits.get("l5") is not None and l5_n:
            factors.append(f"L5 {l5_hits}/{l5_n} {side}s ({rec_hits['l5']:.0%})")
        if rec_hits.get("l10") is not None and l10_n:
            factors.append(f"L10 {l10_hits}/{l10_n} {side}s ({rec_hits['l10']:.0%})")
        if rec_hits.get("season") is not None:
            factors.append(f"Season hit {rec_hits['season']:.0%}")
        if rec_hits.get("vs_opp") is not None:
            factors.append(
                f"vs {opponent} historically {rec_hits['vs_opp']:.0%} "
                f"({rates.get('sample_games_vs_opp') or 0} g)"
            )
        elif rec_hits.get("vs_similar") is not None:
            factors.append(
                f"vs similar defenses {rec_hits['vs_similar']:.0%} "
                f"({rates.get('sample_games_vs_similar') or 0} g)"
            )
        if context.get("team_implied_total") is not None:
            factors.append(f"Team implied total {context['team_implied_total']}")
        if context.get("team_spread") is not None:
            factors.append(f"Spread {context['team_spread']:+.1f}")
        if matchup_note:
            factors.append(matchup_note)
        if weather.get("note"):
            factors.append(str(weather["note"]))
        if kickoff.get("kickoff_et"):
            factors.append(f"Kickoff {kickoff['kickoff_et']}")
        sides.append(
            {
                **row,
                "sport": "nfl",
                "game_id": str(game.get("game_id") or ""),
                "game_date": game_date.isoformat(),
                "matchup": f"{away} @ {home}",
                "team": team,
                "opponent": opponent,
                "position": position,
                "player_id": athlete_id,
                "recommended_side": side,
                "recommended_odds": odds,
                "model_projection": projection.get("model_projection"),
                "model_probability": model_p,
                "model_probability_over": model_probs.get("model_probability_over"),
                "model_probability_under": model_probs.get("model_probability_under"),
                "market_probability": market_p,
                "edge": edge,
                "prop_score": scored["prop_score"],
                "score": scored["prop_score"],
                "line_strength": scored["line_strength"],
                "line_strength_label": LINE_STRENGTH_LABELS.get(
                    str(scored["line_strength"]), str(scored["line_strength"])
                ),
                "actionable": scored["actionable"],
                "risk_flag": risk_flags[0] if risk_flags else None,
                "risk_flags": risk_flags,
                "factors": factors,
                "bookmaker": row.get("bookmaker"),
                "bookmaker_label": row.get("bookmaker_label"),
                "projection_confidence": projection.get("projection_confidence"),
                "confidence_pct": conf["confidence_pct"],
                "confidence_label": conf["confidence_label"],
                "cash_probability": conf["cash_probability"],
                "recommended_probability": conf["cash_probability"] if conf["cash_probability"] is not None else model_p,
                **rates,
                "recommended_hit_rate": rec_hits.get("l10"),
                "hit_rate_l5": rec_hits.get("l5"),
                "hit_rate_l10": rec_hits.get("l10"),
                "hit_rate_season": rec_hits.get("season"),
                "analysis": {
                    "type": "nfl",
                    "usage": {
                        "l3_avg": projection.get("l3_avg"),
                        "season_avg": projection.get("season_avg"),
                        "role_shift": projection.get("role_shift"),
                        "sample_games": projection.get("sample_games"),
                    },
                    "environment": {
                        **context,
                        "weather": weather,
                        "kickoff": kickoff,
                        "defense": defense,
                        "defense_multiplier": def_mult,
                        "weather_multiplier": wx_mult,
                        "kickoff_multiplier": ko_mult,
                        "env_multiplier": projection.get("env_multiplier"),
                    },
                    "matchup": {
                        "opponent": opponent,
                        "home": is_home,
                        "label": matchup_note,
                    },
                    "hit_rates": rec_hits,
                    "recent_games": rates.get("recent_games") or [],
                    "hit_window": rates.get("hit_window"),
                    "risks": risk_flags,
                    "injury": injury,
                },
                "prop_model_version": NFL_PROP_MODEL_VERSION,
            }
        )

    if not sides:
        return []
    sides.sort(
        key=lambda r: (
            r.get("cash_probability") is None,
            -(r.get("cash_probability") or 0),
            -(r.get("model_probability") or 0),
        )
    )
    best = sides[0]
    best["sides"] = [
        {
            "side": s["recommended_side"],
            "odds": s["recommended_odds"],
            "model_probability": s["model_probability"],
            "market_probability": s["market_probability"],
            "edge": s["edge"],
        }
        for s in sides
    ]
    return [best]


def _assemble_game_payload(
    event: dict[str, Any],
    *,
    game: dict[str, Any],
    game_date: date,
    book: str,
    markets: str,
) -> dict[str, Any]:
    raw_rows = parse_nfl_event_props(event, book)
    env = _odds_environment(game, game_date)
    scored: list[dict[str, Any]] = []
    for row in raw_rows:
        row["bookmaker"] = book
        row["bookmaker_label"] = _bookmaker_label(book)
        scored.extend(score_nfl_prop_row(row, game=game, game_date=game_date, env=env))
    scored.sort(
        key=lambda r: (
            -(float(r.get("confidence_pct") or 0)),
            -(r.get("cash_probability") or 0),
            -(r.get("edge") or 0),
        )
    )
    home = _team_abbr(game, "home")
    away = _team_abbr(game, "away")
    return {
        "sport": "nfl",
        "game_id": str(game.get("game_id") or ""),
        "date": game_date.isoformat(),
        "matchup": f"{away} @ {home}",
        "home_team": home,
        "away_team": away,
        "props": scored,
        "fetched_at": _utc_now(),
        "source": "the_odds_api_live",
        "status": "ok" if scored else "empty",
        "markets_requested": markets,
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
        "odds_event_id": event.get("id"),
        "pregame_only": True,
        "prop_model_version": NFL_PROP_MODEL_VERSION,
    }


def _needs_nfl_rescore(payload: dict[str, Any] | None) -> bool:
    props = list((payload or {}).get("props") or [])
    if not props:
        return False
    sample = props[0]
    version = int(payload.get("prop_model_version") or sample.get("prop_model_version") or 0)
    return (
        version < NFL_PROP_MODEL_VERSION
        or sample.get("hit_rate_l5") is None
        or sample.get("confidence_pct") is None
    )


def _rescore_cached_payload(
    payload: dict[str, Any],
    *,
    game: dict[str, Any],
    game_date: date,
    book: str,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Re-apply ESPN hit rates / matchup / weather on cached lines (no Odds API)."""
    env = _odds_environment(game, game_date)
    scored: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for prop in payload.get("props") or []:
        key = (
            prop.get("player"),
            prop.get("market_type"),
            prop.get("line"),
            prop.get("line_kind") or "main",
        )
        if key in seen:
            continue
        seen.add(key)
        row = {
            "player": prop.get("player"),
            "market_type": prop.get("market_type"),
            "market_label": prop.get("market_label") or market_label(str(prop.get("market_type") or "")),
            "line": prop.get("line"),
            "line_kind": prop.get("line_kind") or "main",
            "over_odds": prop.get("over_odds"),
            "under_odds": prop.get("under_odds"),
            "complete_market": prop.get("complete_market"),
            "offered_books": prop.get("offered_books"),
            "bookmaker": book,
            "bookmaker_label": _bookmaker_label(book),
        }
        scored.extend(score_nfl_prop_row(row, game=game, game_date=game_date, env=env))
    scored.sort(
        key=lambda r: (
            -(float(r.get("confidence_pct") or 0)),
            -(r.get("cash_probability") or 0),
            -(r.get("edge") or 0),
        )
    )
    out = {**payload, "props": scored, "rescored_at": _utc_now(), "prop_model_version": NFL_PROP_MODEL_VERSION}
    if cache_path is not None:
        _write_json(cache_path, out)
    return out


def _cached_game_props(
    payload: dict[str, Any] | None,
    *,
    game: dict[str, Any],
    game_date: date,
    book: str,
    cache_path: Path,
) -> dict[str, Any] | None:
    if not payload or payload.get("props") is None:
        return payload
    if _needs_nfl_rescore(payload):
        try:
            return _rescore_cached_payload(
                payload, game=game, game_date=game_date, book=book, cache_path=cache_path
            )
        except Exception:
            logger.exception("NFL prop cache rescore failed for %s", cache_path)
            return payload
    return payload


def build_nfl_game_props(
    game_id: str,
    game_date: date | None = None,
    *,
    refresh: bool = False,
    bookmaker: str | None = None,
    include_alternates: bool = False,
) -> dict[str, Any] | None:
    from app.services.schedule_nfl import get_nfl_game

    game_date = game_date or slate_today()
    book = _normalize_bookmaker(bookmaker)
    detail = get_nfl_game(str(game_id), game_date)
    if detail is None:
        cached = _load_json(_cache_path(game_date, str(game_id), book))
        return cached
    game = detail.get("game") or detail
    if _game_started(game) and not refresh:
        cached = _load_json(_cache_path(game_date, str(game_id), book))
        if cached:
            return {**cached, "status": "started", "message": "Pregame props hidden after kickoff."}
        return {
            "sport": "nfl",
            "game_id": str(game_id),
            "date": game_date.isoformat(),
            "props": [],
            "status": "started",
            "message": "Pregame props are not shown after kickoff.",
        }

    cache_path = _cache_path(game_date, str(game_id), book)
    if not refresh:
        cached = _load_json(cache_path)
        if cached and cached.get("props") is not None:
            return _cached_game_props(
                cached, game=game, game_date=game_date, book=book, cache_path=cache_path
            ) or cached
        return {
            "sport": "nfl",
            "game_id": str(game_id),
            "date": game_date.isoformat(),
            "props": [],
            "status": "empty",
            "empty_reason": "no_cache",
            "message": (
                "NFL prop lines are not cached for this game yet. "
                "Click Refresh on the props page (uses Odds API credits) or wait for the morning job."
            ),
            "bookmaker": book,
            "bookmaker_label": _bookmaker_label(book),
        }

    markets = SLATE_NFL_PROP_MARKETS
    if include_alternates:
        markets = f"{markets},{ALTERNATE_NFL_PROP_MARKETS}"
    events, err = _load_events(game_date, refresh=refresh)
    matched = _match_odds_event(events, _team_abbr(game, "home"), _team_abbr(game, "away"))
    if not matched:
        empty = {
            "sport": "nfl",
            "game_id": str(game_id),
            "date": game_date.isoformat(),
            "props": [],
            "status": "empty",
            "message": err or "Sportsbooks haven't posted NFL player props for this game yet.",
            "bookmaker": book,
            "bookmaker_label": _bookmaker_label(book),
        }
        _write_json(cache_path, empty)
        return empty

    sport_key = str(matched.get("odds_sport_key") or SPORT_NFL)
    result = fetch_nfl_event_props_if_allowed(
        str(matched.get("id") or ""),
        markets=markets,
        bookmakers=None if book == DEFAULT_PROP_BOOKMAKER else book,
        sport=sport_key,
    )
    if result.denied or result.error or not result.events:
        cached = _load_json(cache_path)
        if cached:
            return {**cached, "stale_cache": True, "message": result.denied_reason or result.error}
        return {
            "sport": "nfl",
            "game_id": str(game_id),
            "props": [],
            "status": "error",
            "message": result.denied_reason or result.error or "NFL player props unavailable.",
        }
    payload = _assemble_game_payload(
        result.events[0],
        game=game,
        game_date=game_date,
        book=book,
        markets=markets,
    )
    _write_json(cache_path, payload)
    return payload


def _load_nfl_props_schedule(game_date: date) -> tuple[date, dict[str, Any]]:
    """Load NFL games for props.

    ``get_nfl_schedule(..., auto_resolve=True)`` only looks ahead when the
    date argument is None. Props always pass an explicit date, so resolve
    today's slate here (Thu–Mon look-ahead) instead of scanning an empty day.
    """
    from app.services.schedule_nfl import get_nfl_schedule, resolve_nfl_slate_date

    if game_date == slate_today():
        resolved, _days = resolve_nfl_slate_date(game_date)
        schedule = get_nfl_schedule(resolved)
    else:
        schedule = get_nfl_schedule(game_date)
    out = date.fromisoformat(str(schedule.get("date") or game_date.isoformat()))
    return out, schedule


def refresh_nfl_props_slate(
    game_date: date | None = None,
    *,
    bookmaker: str | None = None,
    force: bool = False,
    include_alternates: bool | None = None,
) -> dict[str, Any]:
    game_date = game_date or slate_today()
    resolved, schedule = _load_nfl_props_schedule(game_date)
    book = _normalize_bookmaker(bookmaker)
    games = [g for g in (schedule.get("games") or []) if g.get("game_id") and not _game_started(g)]
    fetched = 0
    games_with_props = 0
    pending: list[str] = []
    quota_stopped = False
    for game in games:
        gid = str(game["game_id"])
        cache_path = _cache_path(resolved, gid, book)
        if not force and cache_path.exists():
            fetched += 1
            cached = _load_json(cache_path) or {}
            if cached.get("props"):
                games_with_props += 1
            continue
        payload = build_nfl_game_props(
            gid,
            resolved,
            refresh=True,
            bookmaker=book,
            include_alternates=bool(include_alternates),
        )
        if payload and payload.get("status") == "error" and "quota" in str(payload.get("message") or "").lower():
            quota_stopped = True
            pending.append(gid)
            break
        if payload and payload.get("props") is not None:
            fetched += 1
            if payload.get("props"):
                games_with_props += 1
        else:
            pending.append(gid)
    return {
        "sport": "nfl",
        "date": resolved.isoformat(),
        "bookmaker": book,
        "games_on_slate": len(games),
        "games_with_props": games_with_props,
        "games_fetched": fetched,
        "pending_game_ids": pending,
        "quota_stopped": quota_stopped,
    }


def search_nfl_daily_props(
    game_date: date | None = None,
    *,
    bookmaker: str | None = None,
    market_type: str | None = None,
    min_odds: int | None = None,
    line_kind: str | None = None,
    line_value: float | None = None,
    side: str | None = None,
    actionable_only: bool = False,
    very_strong_only: bool = False,
    include_alternates: bool = False,
    sort: str = "confidence",
    risk: str | None = None,
    min_score: int | None = None,
    min_edge: float | None = None,
    position: str | None = None,
    team: str | None = None,
    limit: int = 200,
    scan: bool = False,
    refresh: bool = False,
    min_hit_l5: float | None = None,
    min_hit_l10: float | None = None,
) -> dict[str, Any]:
    """NFL explorer search with L5/L10/season hit-rate filters and main vs alt lines."""
    game_date = game_date or slate_today()
    book = _normalize_bookmaker(bookmaker)
    resolved, schedule = _load_nfl_props_schedule(game_date)
    games = list(schedule.get("games") or [])
    if refresh or scan:
        refresh_nfl_props_slate(
            resolved,
            bookmaker=book,
            force=refresh,
            include_alternates=include_alternates,
        )

    pool: list[dict[str, Any]] = []
    games_with_props = 0
    games_started = 0
    cached_any = False
    quota_hit = False
    for game in games:
        gid = str(game.get("game_id") or "")
        if not gid:
            continue
        if _game_started(game):
            games_started += 1
            continue
        payload = _load_json(_cache_path(resolved, gid, book))
        if payload:
            payload = _cached_game_props(
                payload,
                game=game,
                game_date=resolved,
                book=book,
                cache_path=_cache_path(resolved, gid, book),
            ) or payload
        if not payload:
            if scan or refresh:
                payload = build_nfl_game_props(
                    gid,
                    resolved,
                    refresh=False,
                    bookmaker=book,
                    include_alternates=include_alternates,
                )
            else:
                continue
        if payload:
            cached_any = True
            msg = str(payload.get("message") or "").lower()
            if payload.get("status") == "error" and "quota" in msg:
                quota_hit = True
        props = list((payload or {}).get("props") or [])
        if props:
            games_with_props += 1
        pool.extend(props)

    def _ok(prop: dict[str, Any]) -> bool:
        if market_type and prop.get("market_type") != market_type:
            return False
        if line_kind and line_kind != "both" and prop.get("line_kind") != line_kind:
            return False
        if line_value is not None and prop.get("line") != line_value:
            return False
        if side and side != "both" and prop.get("recommended_side") != side:
            return False
        if min_odds is not None and prop.get("recommended_odds") is not None:
            if int(prop["recommended_odds"]) < int(min_odds):
                return False
        if actionable_only and not prop.get("actionable"):
            return False
        if very_strong_only and prop.get("line_strength") not in ("very_strong", "elite"):
            return False
        if min_score is not None and (prop.get("prop_score") or 0) < min_score:
            return False
        if min_edge is not None and (prop.get("edge") or -1) < min_edge:
            return False
        if position:
            want = position_group(position)
            if want == "WR":
                if prop.get("position") not in ("WR", "TE"):
                    return False
            elif prop.get("position") != want:
                return False
        if team and normalize_nfl_team(prop.get("team") or "") != normalize_nfl_team(team):
            return False
        if risk == "low" and prop.get("risk_flag"):
            return False
        if min_hit_l5 is not None:
            hr = prop.get("hit_rate_l5")
            if hr is None or float(hr) < float(min_hit_l5):
                return False
        if min_hit_l10 is not None:
            hr = prop.get("hit_rate_l10")
            if hr is None or float(hr) < float(min_hit_l10):
                return False
        return True

    filtered = [p for p in pool if _ok(p)]

    def _side_hit(prop: dict[str, Any], key: str) -> float:
        val = prop.get(key)
        try:
            return float(val) if val is not None else -1.0
        except (TypeError, ValueError):
            return -1.0

    def _cash_key(prop: dict[str, Any]) -> tuple[float, float, float]:
        return (
            -(float(prop.get("confidence_pct") or 0)),
            -(float(prop.get("cash_probability") or 0)),
            -(float(prop.get("model_probability") or 0)),
        )

    if sort == "edge":
        filtered.sort(key=lambda p: (p.get("edge") is None, -(p.get("edge") or 0)))
    elif sort == "hit_l5":
        filtered.sort(key=lambda p: _side_hit(p, "hit_rate_l5"), reverse=True)
    elif sort == "hit_l10":
        filtered.sort(key=lambda p: _side_hit(p, "hit_rate_l10"), reverse=True)
    elif sort == "risk_asc":
        filtered.sort(key=lambda p: (1 if p.get("risk_flag") else 0, _cash_key(p)))
    elif sort == "risk_desc":
        filtered.sort(key=lambda p: (0 if p.get("risk_flag") else 1, _cash_key(p)))
    else:
        filtered.sort(key=_cash_key)
    empty_reason = None
    if not games:
        empty_reason = "no_slate"
    elif games_started == len(games) and games:
        empty_reason = "kickoff"
    elif not pool and quota_hit:
        empty_reason = "quota"
    elif not pool and not cached_any:
        empty_reason = "no_cache"
    elif not pool:
        empty_reason = "no_offers"
    elif not filtered:
        empty_reason = "filters"
    if filtered:
        try:
            from app.services.prop_pick_tracker import log_offered_props

            log_offered_props(filtered, resolved.isoformat(), source="nfl_daily_props")
        except Exception:
            logger.exception("NFL prop tracker log failed")
    return {
        "sport": "nfl",
        "date": resolved.isoformat(),
        "requested_date": game_date.isoformat(),
        "bookmaker": book,
        "bookmaker_label": _bookmaker_label(book),
        "props": filtered[:limit],
        "total_matched": len(filtered),
        "games_on_slate": len(games),
        "games_with_props": games_with_props,
        "games_started": games_started,
        "empty_reason": empty_reason,
        "message": _empty_message(
            empty_reason,
            games_on_slate=len(games),
            games_with_props=games_with_props,
        ),
    }


def _empty_message(
    reason: str | None,
    *,
    games_on_slate: int = 0,
    games_with_props: int = 0,
) -> str | None:
    if reason == "no_slate":
        return "No NFL games on the current slate."
    if reason == "kickoff":
        return "Pregame NFL player props are hidden after kickoff."
    if reason == "quota":
        return (
            "Odds API quota is exhausted. NFL prop lines will refresh after quota resets. "
            f"{games_with_props}/{games_on_slate} games currently cached."
        )
    if reason == "no_cache":
        return (
            f"{games_on_slate} NFL games are on the slate, but prop lines are not cached yet. "
            "Click Refresh (uses Odds API credits) or wait for the morning job."
        )
    if reason == "no_offers":
        return (
            f"Sportsbooks haven't posted NFL player props yet "
            f"({games_with_props}/{games_on_slate} games with lines)."
        )
    if reason == "filters":
        return "No props match your current filters."
    return None


def list_nfl_bookmakers() -> list[dict[str, Any]]:
    return list_static_prop_bookmakers()
