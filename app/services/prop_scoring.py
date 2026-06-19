"""Heuristic player prop scoring from recent MLB game logs + matchup context."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from app.data.pitcher_lookup import lookup_pitcher_rates

logger = logging.getLogger(__name__)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
_http_client: httpx.Client | None = None
MIN_GAMES_FOR_SCORE = 5
MIN_ALLTIME_SEASON = 2018
ALLTIME_SEASONS_BACK = 3
MIN_ACTIONABLE_HIT_RATE = 0.55
TRAP_UNOFFERED_GAP = 0.20
TRAP_UNOFFERED_MIN = 0.70

MARKET_LABELS: dict[str, str] = {
    "batter_hits": "Hits",
    "batter_total_bases": "Total bases",
    "batter_rbis": "RBIs",
    "batter_runs_scored": "Runs",
    "batter_home_runs": "Home runs",
    "pitcher_strikeouts": "Strikeouts",
    "pitcher_hits_allowed": "Hits allowed",
    "pitcher_earned_runs": "Earned runs",
    "pitcher_outs": "Outs recorded",
}

MARKET_STAT: dict[str, tuple[str, str]] = {
    "batter_hits": ("hitting", "hits"),
    "batter_total_bases": ("hitting", "totalBases"),
    "batter_rbis": ("hitting", "rbi"),
    "batter_runs_scored": ("hitting", "runs"),
    "batter_home_runs": ("hitting", "homeRuns"),
    "pitcher_strikeouts": ("pitching", "strikeOuts"),
    "pitcher_hits_allowed": ("pitching", "hits"),
    "pitcher_earned_runs": ("pitching", "earnedRuns"),
    "pitcher_outs": ("pitching", "_outs"),
}


def market_label(market_type: str) -> str:
    return MARKET_LABELS.get(market_type, market_type.replace("_", " ").title())


def _http_client_get() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=20.0)
    return _http_client


def warm_scoring_cache(
    players: set[str] | list[str],
    prop_rows: list[dict[str, Any]],
    season: int,
) -> None:
    """Pre-fetch MLB game logs for unique player/market pairs before scoring loop."""
    needed: set[tuple[int, str, str]] = set()
    for name in players:
        pid = _search_player_id(name)
        if pid is None:
            continue
        for row in prop_rows:
            if row.get("player") != name:
                continue
            mapping = MARKET_STAT.get(str(row.get("market_type", "")))
            if mapping:
                needed.add((pid, mapping[0], mapping[1]))
    for pid, group, stat_key in needed:
        for yr in range(season - ALLTIME_SEASONS_BACK + 1, season + 1):
            if yr >= MIN_ALLTIME_SEASON:
                _season_game_log_values(pid, group, stat_key, yr)


def _parse_innings_outs(ip_value: object) -> float | None:
    if ip_value is None:
        return None
    text = str(ip_value).strip()
    if not text:
        return None
    if "." in text:
        whole, frac = text.split(".", 1)
        try:
            return float(int(whole)) * 3.0 + float(int(frac))
        except ValueError:
            return None
    try:
        return float(text) * 3.0
    except ValueError:
        return None


def _stat_value(group: str, stat_key: str, stat: dict[str, Any]) -> float | None:
    if stat_key == "_outs":
        return _parse_innings_outs(stat.get("inningsPitched"))
    raw = stat.get(stat_key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _side_hits(stat: float, line: float, side: str) -> bool:
    if side == "over":
        return stat > line
    return stat < line


def _hit_rates(values: list[float], line: float) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    n = len(values)
    over = sum(1 for v in values if _side_hits(v, line, "over")) / n
    under = sum(1 for v in values if _side_hits(v, line, "under")) / n
    return round(over, 3), round(under, 3)


def displays_as_perfect_pct(rate: float | None) -> bool:
    """Match UI chips: Math.round(rate * 100) === 100."""
    if rate is None:
        return False
    return round(rate * 100) >= 100


def side_form_hit_rates(
    prop: dict[str, Any],
    side: str | None = None,
) -> tuple[float | None, float | None, float | None]:
    pick = side or prop.get("recommended_side") or "over"
    if pick == "over":
        return (
            prop.get("hit_rate_over_l5"),
            prop.get("hit_rate_over_l10"),
            prop.get("hit_rate_over_season"),
        )
    return (
        prop.get("hit_rate_under_l5"),
        prop.get("hit_rate_under_l10"),
        prop.get("hit_rate_under_season"),
    )


def is_perfect_l5_l10_season(
    l5: float | None,
    l10: float | None,
    season: float | None,
) -> bool:
    return (
        displays_as_perfect_pct(l5)
        and displays_as_perfect_pct(l10)
        and displays_as_perfect_pct(season)
    )


def refresh_prop_line_strength(prop: dict[str, Any]) -> dict[str, Any]:
    """Re-derive very strong from stored L5/L10/season (fixes stale cached labels)."""
    if not prop.get("actionable"):
        return prop
    side = prop.get("recommended_side")
    if not side:
        return prop
    l5, l10, season = side_form_hit_rates(prop, side)
    if not is_perfect_l5_l10_season(l5, l10, season):
        return prop
    odds = prop.get("recommended_odds")
    odds_note = f" · Book {_format_american_odds(odds)}" if odds is not None else ""
    return {
        **prop,
        "line_strength": "very_strong",
        "line_strength_label": "Very strong",
        "line_insight": f"100% L5 · L10 · Season{odds_note}",
    }


@lru_cache(maxsize=256)
def _search_player_id(player_name: str) -> int | None:
    name = (player_name or "").strip()
    if not name:
        return None
    url = f"{MLB_STATS_BASE}/people/search"
    try:
        response = _http_client_get().get(url, params={"names": name})
        response.raise_for_status()
        people = response.json().get("people") or []
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("MLB player search failed for %s: %s", name, exc)
        return None
    if not people:
        return None
    exact = next(
        (p for p in people if str(p.get("fullName", "")).lower() == name.lower()),
        None,
    )
    person = exact or people[0]
    pid = person.get("id")
    return int(pid) if pid is not None else None


@lru_cache(maxsize=256)
def _player_team_id(player_id: int) -> int | None:
    url = f"{MLB_STATS_BASE}/people/{player_id}"
    try:
        response = _http_client_get().get(
            url, params={"hydrate": "currentTeam", "fields": "currentTeam,id"}
        )
        response.raise_for_status()
        person = response.json().get("people", [{}])[0]
    except (httpx.HTTPError, ValueError, IndexError):
        return None
    team = person.get("currentTeam") or {}
    tid = team.get("id")
    return int(tid) if tid is not None else None


def clear_prop_scoring_cache() -> None:
    """Clear LRU caches (tests)."""
    _search_player_id.cache_clear()
    _player_team_id.cache_clear()
    _season_game_log_values.cache_clear()


@lru_cache(maxsize=256)
def _season_game_log_values(player_id: int, group: str, stat_key: str, season: int) -> tuple[float, ...]:
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "gameLog", "group": group, "season": season}
    try:
        response = _http_client_get().get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return tuple()

    stats_blocks = payload.get("stats") or []
    if not stats_blocks:
        return tuple()
    splits = stats_blocks[0].get("splits") or []
    values: list[float] = []
    for split in splits:
        stat = split.get("stat") or {}
        val = _stat_value(group, stat_key, stat)
        if val is not None:
            values.append(val)
    return tuple(values)


def _alltime_game_log_values(player_id: int, group: str, stat_key: str, season: int) -> tuple[float, ...]:
    """Merge recent season game logs for all-time hit-rate context."""
    merged: list[float] = []
    for yr in range(season - ALLTIME_SEASONS_BACK + 1, season + 1):
        if yr < MIN_ALLTIME_SEASON:
            continue
        merged.extend(_season_game_log_values(player_id, group, stat_key, yr))
    return tuple(merged)


def _matchup_rank_points(
    market_type: str,
    *,
    opposing_pitcher_era: float | None,
    pitcher_k_rate: float | None,
) -> tuple[float, list[str]]:
    """Perspective adjustment for ranking: soft/tough matchups can outweigh raw L10."""
    notes: list[str] = []
    pts = 0.0
    if market_type.startswith("batter_") and opposing_pitcher_era is not None:
        era = opposing_pitcher_era
        if era >= 5.0:
            pts = 22.0
            notes.append(f"Opposing starter ERA {era:.2f} (very soft matchup)")
        elif era >= 4.5:
            pts = 18.0
            notes.append(f"Opposing starter ERA {era:.2f} (soft matchup)")
        elif era <= 2.9:
            pts = -22.0
            notes.append(f"Opposing starter ERA {era:.2f} (elite arm)")
        elif era <= 3.2:
            pts = -18.0
            notes.append(f"Opposing starter ERA {era:.2f} (tough matchup)")
        else:
            # Linear tilt between neutral (3.85) and soft/tough bands
            tilt = (era - 3.85) / 0.65
            pts = max(-12.0, min(12.0, tilt * 12.0))
            if pts >= 4:
                notes.append(f"Opposing starter ERA {era:.2f} (favorable)")
            elif pts <= -4:
                notes.append(f"Opposing starter ERA {era:.2f} (unfavorable)")
    if market_type == "pitcher_strikeouts" and pitcher_k_rate is not None:
        if pitcher_k_rate >= 6.0:
            pts += 12.0
            notes.append(f"Season {pitcher_k_rate:.1f} K/game")
        elif pitcher_k_rate <= 3.5:
            pts -= 12.0
            notes.append(f"Season {pitcher_k_rate:.1f} K/game (cold)")
    return pts, notes


def _matchup_adjustment(
    market_type: str,
    *,
    opposing_pitcher_era: float | None,
    pitcher_k_rate: float | None,
) -> tuple[float, list[str]]:
    pts, notes = _matchup_rank_points(
        market_type,
        opposing_pitcher_era=opposing_pitcher_era,
        pitcher_k_rate=pitcher_k_rate,
    )
    return pts, notes


def _compute_rank_score(*, hit_rate: float, **_kwargs: Any) -> float:
    """Form score for display and sorting: L10 hit rate as 0–100."""
    return round(hit_rate * 100.0, 1)


def _choose_actionable_side(
    *,
    line: float,
    over_odds: int | None,
    under_odds: int | None,
    l5_over: float | None,
    l5_under: float | None,
    l10_over: float | None,
    l10_under: float | None,
    season_over: float | None,
    season_under: float | None,
) -> dict[str, Any]:
    offered: list[tuple[str, float, int]] = []
    if over_odds is not None and l10_over is not None:
        offered.append(("over", l10_over, over_odds))
    if under_odds is not None and l10_under is not None:
        offered.append(("under", l10_under, under_odds))

    if not offered:
        return {
            "recommended_side": None,
            "recommended_odds": None,
            "recommended_hit_rate": None,
            "actionable": False,
            "actionable_reason": "No offered side with enough game history",
        }

    best_offered = max(offered, key=lambda x: x[1])
    side, hit_rate, odds = best_offered

    unoffered: list[tuple[str, float]] = []
    if over_odds is None and l10_over is not None:
        unoffered.append(("over", l10_over))
    if under_odds is None and l10_under is not None:
        unoffered.append(("under", l10_under))

    if unoffered:
        best_unoffered = max(unoffered, key=lambda x: x[1])
        if (
            best_unoffered[1] >= TRAP_UNOFFERED_MIN
            and best_unoffered[1] - hit_rate >= TRAP_UNOFFERED_GAP
        ):
            return {
                "recommended_side": side,
                "recommended_odds": odds,
                "recommended_hit_rate": hit_rate,
                "actionable": False,
                "actionable_reason": (
                    f"{best_unoffered[0].title()} hits {best_unoffered[1]:.0%} L10 "
                    f"but only {side.title()} is listed"
                ),
            }

    if hit_rate < MIN_ACTIONABLE_HIT_RATE:
        return {
            "recommended_side": side,
            "recommended_odds": odds,
            "recommended_hit_rate": hit_rate,
            "actionable": False,
            "actionable_reason": f"L10 {hit_rate:.0%} on offered {side} — mixed form",
        }

    return {
        "recommended_side": side,
        "recommended_odds": odds,
        "recommended_hit_rate": hit_rate,
        "actionable": True,
        "actionable_reason": None,
    }


def score_prop(
    *,
    player: str,
    market_type: str,
    line: float,
    over_odds: int | None,
    under_odds: int | None,
    season: int,
    opposing_pitcher: str | None = None,
    opposing_pitcher_era: float | None = None,
) -> dict[str, Any]:
    """Score a prop line using L5/L10/season logs and matchup context."""
    empty = {
        "score": None,
        "recommended_side": None,
        "recommended_odds": None,
        "recommended_hit_rate": None,
        "actionable": False,
        "actionable_reason": None,
        "hit_rate_over_l5": None,
        "hit_rate_under_l5": None,
        "hit_rate_over_l10": None,
        "hit_rate_under_l10": None,
        "hit_rate_over_season": None,
        "hit_rate_under_season": None,
        "hit_rate_over_alltime": None,
        "hit_rate_under_alltime": None,
        "recent_avg_l10": None,
        "sample_games_season": 0,
        "sample_games_alltime": 0,
        "factors": [],
        "market_label": market_label(market_type),
        "line_strength": None,
        "line_strength_label": None,
        "line_insight": None,
        "rank_score": None,
        "matchup_adjustment": None,
    }

    mapping = MARKET_STAT.get(market_type)
    if mapping is None:
        return empty

    group, stat_key = mapping
    player_id = _search_player_id(player)
    if player_id is None:
        return {**empty, "factors": ["Could not match player to MLB stats"]}

    values = list(_season_game_log_values(player_id, group, stat_key, season))
    if len(values) < MIN_GAMES_FOR_SCORE:
        return {
            **empty,
            "sample_games_season": len(values),
            "factors": [f"Only {len(values)} games logged this season"],
        }

    l5 = values[:5]
    l10 = values[:10]
    l5_over, l5_under = _hit_rates(l5, line)
    l10_over, l10_under = _hit_rates(l10, line)
    season_over, season_under = _hit_rates(values, line)
    recent_avg = sum(l10) / len(l10)

    alltime_values = list(_alltime_game_log_values(player_id, group, stat_key, season))
    alltime_over, alltime_under = _hit_rates(alltime_values, line)

    choice = _choose_actionable_side(
        line=line,
        over_odds=over_odds,
        under_odds=under_odds,
        l5_over=l5_over,
        l5_under=l5_under,
        l10_over=l10_over,
        l10_under=l10_under,
        season_over=season_over,
        season_under=season_under,
    )

    side = choice["recommended_side"]
    hit_rate = choice["recommended_hit_rate"]
    factors: list[str] = []
    if side and hit_rate is not None:
        factors.append(
            f"L5 {side} { _side_rate_label(side, l5_over, l5_under)} · "
            f"L10 {hit_rate:.0%} · Season {_side_rate_label(side, season_over, season_under)}"
        )
    if choice.get("actionable_reason"):
        factors.append(choice["actionable_reason"])

    era = opposing_pitcher_era
    if era is None and opposing_pitcher:
        era, _fip = lookup_pitcher_rates(opposing_pitcher, season, {})

    k_rate = recent_avg if market_type == "pitcher_strikeouts" else None
    adj, matchup_notes = _matchup_adjustment(
        market_type,
        opposing_pitcher_era=era,
        pitcher_k_rate=k_rate,
    )
    factors.extend(matchup_notes)

    score: float | None = None
    rank_score: float | None = None
    if choice["actionable"] and hit_rate is not None and side:
        rank_score = _compute_rank_score(hit_rate=hit_rate)
        score = rank_score

    side_l5 = l5_over if side == "over" else l5_under if side == "under" else None
    side_l10 = l10_over if side == "over" else l10_under if side == "under" else None
    side_season = season_over if side == "over" else season_under if side == "under" else None
    line_strength = _prop_line_strength(
        market_type=market_type,
        side=side,
        hit_rate=hit_rate,
        recent_avg=recent_avg,
        line=line,
        actionable=choice["actionable"],
        actionable_reason=choice.get("actionable_reason"),
        matchup_notes=matchup_notes,
        l5_rate=side_l5,
        l10_rate=side_l10,
        season_rate=side_season,
        recommended_odds=choice["recommended_odds"],
    )

    return {
        "score": score,
        "recommended_side": side,
        "recommended_odds": choice["recommended_odds"],
        "recommended_hit_rate": hit_rate,
        "actionable": choice["actionable"],
        "actionable_reason": choice["actionable_reason"],
        "hit_rate_over_l5": l5_over,
        "hit_rate_under_l5": l5_under,
        "hit_rate_over_l10": l10_over,
        "hit_rate_under_l10": l10_under,
        "hit_rate_over_season": season_over,
        "hit_rate_under_season": season_under,
        "hit_rate_over_alltime": alltime_over,
        "hit_rate_under_alltime": alltime_under,
        "hit_rate_over": l10_over,
        "hit_rate_under": l10_under,
        "recent_avg": round(recent_avg, 2),
        "recent_avg_l10": round(recent_avg, 2),
        "sample_games": len(l10),
        "sample_games_season": len(values),
        "sample_games_alltime": len(alltime_values),
        "factors": factors,
        "market_label": market_label(market_type),
        "rank_score": rank_score,
        "matchup_adjustment": round(adj, 1) if adj else None,
        **line_strength,
    }


def _side_rate_label(side: str, over_rate: float | None, under_rate: float | None) -> str:
    rate = over_rate if side == "over" else under_rate
    return f"{rate:.0%}" if rate is not None else "—"


def _format_american_odds(odds: int | None) -> str:
    if odds is None:
        return "—"
    return f"+{odds}" if odds > 0 else str(odds)


def _prop_line_strength(
    *,
    market_type: str,
    side: str | None,
    hit_rate: float | None,
    recent_avg: float,
    line: float,
    actionable: bool,
    actionable_reason: str | None,
    matchup_notes: list[str],
    l5_rate: float | None = None,
    l10_rate: float | None = None,
    season_rate: float | None = None,
    recommended_odds: int | None = None,
) -> dict[str, str | None]:
    labels = {
        "very_strong": "Very strong",
        "strong": "Strong line",
        "moderate": "Moderate line",
        "weak": "Weak line",
    }
    if (
        actionable
        and side
        and is_perfect_l5_l10_season(l5_rate, l10_rate, season_rate)
    ):
        odds_note = f" · Book {_format_american_odds(recommended_odds)}"
        return {
            "line_strength": "very_strong",
            "line_strength_label": labels["very_strong"],
            "line_insight": f"100% L5 · L10 · Season{odds_note}",
        }

    if not actionable:
        reason = actionable_reason or "Not an actionable prop side"
        return {
            "line_strength": "weak",
            "line_strength_label": labels["weak"],
            "line_insight": reason,
        }

    score = 0
    notes: list[str] = []
    if hit_rate is not None:
        if hit_rate >= 0.65:
            score += 2
            notes.append(f"L10 hit rate {hit_rate:.0%}")
        elif hit_rate >= MIN_ACTIONABLE_HIT_RATE:
            score += 1
            notes.append(f"L10 hit rate {hit_rate:.0%}")

    edge = (recent_avg - line) if side == "over" else (line - recent_avg)
    if edge >= 0.5:
        score += 1
        notes.append(f"Avg {recent_avg:.1f} vs line {line:g}")
    elif edge <= -0.5:
        score -= 1
        notes.append(f"Avg {recent_avg:.1f} vs line {line:g} (tight)")

    for note in matchup_notes:
        lower = note.lower()
        if "era" in lower or "k/game" in lower:
            notes.append(note)
            if "soft" in lower:
                if market_type.startswith("batter_"):
                    score += 1
            elif "tough" in lower or "cold" in lower:
                score -= 1
            elif market_type == "pitcher_strikeouts" and "k/game" in lower and "cold" not in lower:
                score += 1

    if score >= 3:
        level = "strong"
    elif score >= 1:
        level = "moderate"
    else:
        level = "weak"

    insight = " · ".join(notes[:4]) if notes else "Form-only line"
    return {
        "line_strength": level,
        "line_strength_label": labels[level],
        "line_insight": insight,
    }
