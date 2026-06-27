"""Heuristic player prop scoring from recent MLB game logs + matchup context."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from app.data.pitcher_lookup import lookup_pitcher_rates
from app.services.teams_hub import _mlb_player_photo

logger = logging.getLogger(__name__)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
_http_client: httpx.Client | None = None
MIN_GAMES_FOR_SCORE = 5
MIN_ALLTIME_SEASON = 2018
ALLTIME_SEASONS_BACK = 3
MIN_ACTIONABLE_HIT_RATE = 0.60
MIN_L5_HIT_RATE = 0.50
MIN_SEASON_HIT_RATE = 0.50
MIN_TOP_PROP_SCORE = 65.0
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


def _split_game_date_iso(split: dict[str, Any]) -> str:
    raw = split.get("date") or (split.get("game") or {}).get("gameDate") or ""
    return str(raw)[:10]


from app.services.prop_engine.utils import recent_game_window


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
    """Re-derive line strength from confidence tier (fixes stale cached labels)."""
    tier = prop.get("confidence_tier") or prop.get("confidence")
    if tier == "elite":
        return {
            **prop,
            "line_strength": "elite",
            "line_strength_label": "Elite",
        }
    if tier == "very_strong":
        return {
            **prop,
            "line_strength": "very_strong",
            "line_strength_label": "Very strong",
        }
    if tier == "strong" and prop.get("actionable"):
        return {
            **prop,
            "line_strength": "strong",
            "line_strength_label": "Strong line",
        }
    return prop


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
    player_stat_on_date.cache_clear()


@lru_cache(maxsize=512)
def player_stat_on_date(
    player_name: str,
    market_type: str,
    season: int,
    game_date_iso: str,
) -> float | None:
    """Actual stat for one player on a calendar date (None if DNP or unknown market)."""
    mapping = MARKET_STAT.get(market_type)
    if mapping is None:
        return None
    player_id = _search_player_id(player_name)
    if player_id is None:
        return None
    group, stat_key = mapping
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {"stats": "gameLog", "group": group, "season": season}
    try:
        response = _http_client_get().get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    stats_blocks = payload.get("stats") or []
    if not stats_blocks:
        return None
    for split in stats_blocks[0].get("splits") or []:
        raw_date = split.get("date") or (split.get("game") or {}).get("gameDate") or ""
        if str(raw_date)[:10] != game_date_iso:
            continue
        stat = split.get("stat") or {}
        return _stat_value(group, stat_key, stat)
    return None


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
    splits = sorted(
        stats_blocks[0].get("splits") or [],
        key=_split_game_date_iso,
    )
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


def prop_form_average(
    l5: float | None,
    l10: float | None,
    season: float | None,
) -> float:
    """Simple mean of L5, L10, and season hit rates on the recommended side."""
    vals = [float(v) for v in (l5, l10, season) if v is not None]
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 4)


def prop_form_average_from_prop(prop: dict[str, Any]) -> float:
    l5, l10, season = side_form_hit_rates(prop)
    return prop_form_average(l5, l10, season)


def prop_form_composite(
    l5: float | None,
    l10: float | None,
    season: float | None,
    *,
    matchup_adjustment: float | None = None,
) -> float:
    """Alias for prop_form_average (kept for imports)."""
    return prop_form_average(l5, l10, season)


def prop_form_composite_from_prop(prop: dict[str, Any]) -> float:
    return prop_form_average_from_prop(prop)


def qualifies_for_top_props_list(prop: dict[str, Any]) -> bool:
    """Non-very-strong picks must clear a higher composite score bar."""
    if not prop.get("actionable"):
        return False
    l5, l10, season = side_form_hit_rates(prop)
    score = prop.get("score")
    if score is None or float(score) < MIN_TOP_PROP_SCORE:
        return False
    if (l5 or 0) < MIN_L5_HIT_RATE or (season or 0) < MIN_SEASON_HIT_RATE:
        return False
    return prop_form_average_from_prop(prop) >= MIN_ACTIONABLE_HIT_RATE


def _compute_rank_score(
    *,
    hit_rate: float,
    l5: float | None = None,
    season: float | None = None,
    matchup_adjustment: float | None = None,
) -> float:
    """Form score for display and sorting: L5/L10/season average as 0–100."""
    avg = prop_form_average(l5, hit_rate, season)
    return round(avg * 100.0, 1)


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
    offered: list[tuple[str, float, float, float, int]] = []
    if over_odds is not None and l10_over is not None:
        offered.append(
            (
                "over",
                prop_form_average(l5_over, l10_over, season_over),
                l10_over,
                l5_over or 0.0,
                over_odds,
            )
        )
    if under_odds is not None and l10_under is not None:
        offered.append(
            (
                "under",
                prop_form_average(l5_under, l10_under, season_under),
                l10_under,
                l5_under or 0.0,
                under_odds,
            )
        )

    if not offered:
        return {
            "recommended_side": None,
            "recommended_odds": None,
            "recommended_hit_rate": None,
            "actionable": False,
            "actionable_reason": "No offered side with enough game history",
        }

    best_offered = max(offered, key=lambda x: (x[1], x[2], x[3]))
    side, _composite, hit_rate, side_l5, odds = best_offered
    side_season = season_over if side == "over" else season_under

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
            "actionable_reason": f"L10 {hit_rate:.0%} on offered {side} — below {MIN_ACTIONABLE_HIT_RATE:.0%} bar",
        }

    if side_l5 < MIN_L5_HIT_RATE:
        return {
            "recommended_side": side,
            "recommended_odds": odds,
            "recommended_hit_rate": hit_rate,
            "actionable": False,
            "actionable_reason": f"L5 {side_l5:.0%} on offered {side} — recent form too weak",
        }

    if side_season is not None and side_season < MIN_SEASON_HIT_RATE:
        return {
            "recommended_side": side,
            "recommended_odds": odds,
            "recommended_hit_rate": hit_rate,
            "actionable": False,
            "actionable_reason": f"Season {side_season:.0%} on offered {side} — not sustained enough",
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
    sport: str = "mlb",
    game_id: str | None = None,
    team: str | None = None,
    opponent: str | None = None,
) -> dict[str, Any]:
    """Score a prop using the quantitative prop engine (both sides evaluated)."""
    from app.services.prop_engine.evaluate import evaluate_prop

    result = evaluate_prop(
        player=player,
        market_type=market_type,
        line=line,
        over_odds=over_odds,
        under_odds=under_odds,
        season=season,
        opposing_pitcher=opposing_pitcher,
        opposing_pitcher_era=opposing_pitcher_era,
        sport=sport,
        game_id=game_id,
        team=team,
        opponent=opponent,
    )
    result["player"] = player
    result["market_type"] = market_type
    return result


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
