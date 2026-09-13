"""NFL player-prop cashing context: hit rates, matchup, weather, kickoff, confidence.

These are small, documented heuristics on top of ESPN public stats and the
posted line. They are not a claim of a fitted DVOA model. Snap-share and
route participation are still omitted until those feeds exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.prop_engine.utils import recent_game_window

ET = ZoneInfo("America/New_York")

# Approximate NFL league averages used only as a scale for opponent defense.
LEAGUE_PASS_YDS_ALLOWED = 220.0
LEAGUE_RUSH_YDS_ALLOWED = 118.0
LEAGUE_POINTS_ALLOWED = 22.4

LINE_STRENGTH_LABELS = {
    "elite": "Elite",
    "very_strong": "Very strong",
    "strong": "Strong",
    "moderate": "Moderate",
    "low": "Low",
}


def _side_hits(stat: float, line: float, side: str) -> bool:
    if side == "over":
        return stat > line
    return stat < line


def _hit_pair(values: list[float], line: float) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    n = len(values)
    over = sum(1 for v in values if _side_hits(v, line, "over")) / n
    under = sum(1 for v in values if _side_hits(v, line, "under")) / n
    return round(over, 3), round(under, 3)


def hit_rates_vs_line(values: list[float], line: float) -> dict[str, float | None]:
    """L5 / L10 / season hit rates vs a posted line (both sides).

    When *values* is the full prior-game log (including last season), L5/L10
    bleed across seasons. Pass current-season-only values if you want season
    isolated — prefer ``hit_rates_from_log`` for that split.
    """
    clean = [float(v) for v in values if v is not None]
    l5 = recent_game_window(clean, 5)
    l10 = recent_game_window(clean, 10)
    o5, u5 = _hit_pair(l5, line)
    o10, u10 = _hit_pair(l10, line)
    o_s, u_s = _hit_pair(clean, line)
    return {
        "hit_rate_over_l5": o5,
        "hit_rate_under_l5": u5,
        "hit_rate_over_l10": o10,
        "hit_rate_under_l10": u10,
        "hit_rate_over_season": o_s,
        "hit_rate_under_season": u_s,
        "hit_rate_over": o10,
        "hit_rate_under": u10,
        "sample_games_l5": len(l5),
        "sample_games_l10": len(l10),
        "sample_games_season": len(clean),
    }


def hit_rates_from_log(
    entries: list[dict[str, Any]],
    line: float,
    *,
    slate_date: Any,
    opponent: str | None = None,
    similar_opponents: set[str] | None = None,
) -> dict[str, float | None]:
    """L5/L10 from recent games including last season; season = this NFL year only."""
    from app.services.nfl_player_stats import nfl_season_year

    rows = [e for e in entries if e.get("stat_value") is not None]
    all_vals = [float(e["stat_value"]) for e in rows]
    year = nfl_season_year(slate_date)
    season_vals = [
        float(e["stat_value"])
        for e in rows
        if int(e.get("season_year") or nfl_season_year(_as_date(e.get("date")))) == year
    ]
    opp = str(opponent or "").upper()
    vs_opp_vals = [
        float(e["stat_value"])
        for e in rows
        if str(e.get("opponent") or "").upper() == opp and opp
    ]
    similar = {str(a).upper() for a in (similar_opponents or set()) if a}
    vs_sim_vals = [
        float(e["stat_value"])
        for e in rows
        if str(e.get("opponent") or "").upper() in similar
    ]
    l5 = recent_game_window(all_vals, 5)
    l10 = recent_game_window(all_vals, 10)
    o5, u5 = _hit_pair(l5, line)
    o10, u10 = _hit_pair(l10, line)
    o_s, u_s = _hit_pair(season_vals, line)
    o_opp, u_opp = _hit_pair(vs_opp_vals, line)
    o_sim, u_sim = _hit_pair(vs_sim_vals, line)
    return {
        "hit_rate_over_l5": o5,
        "hit_rate_under_l5": u5,
        "hit_rate_over_l10": o10,
        "hit_rate_under_l10": u10,
        "hit_rate_over_season": o_s,
        "hit_rate_under_season": u_s,
        "hit_rate_over_vs_opp": o_opp,
        "hit_rate_under_vs_opp": u_opp,
        "hit_rate_over_vs_similar": o_sim,
        "hit_rate_under_vs_similar": u_sim,
        "hit_rate_over": o10,
        "hit_rate_under": u10,
        "sample_games_l5": len(l5),
        "sample_games_l10": len(l10),
        "sample_games_season": len(season_vals),
        "sample_games_vs_opp": len(vs_opp_vals),
        "sample_games_vs_similar": len(vs_sim_vals),
        "hit_window": "prior_season" if not season_vals and all_vals else "current_season",
    }


def _as_date(raw: Any):
    from datetime import date as date_cls

    if isinstance(raw, date_cls):
        return raw
    try:
        return date_cls.fromisoformat(str(raw)[:10])
    except ValueError:
        return date_cls.today()


def recommended_hit_rates(rates: dict[str, Any], side: str) -> dict[str, float | None]:
    if side == "under":
        return {
            "l5": rates.get("hit_rate_under_l5"),
            "l10": rates.get("hit_rate_under_l10"),
            "season": rates.get("hit_rate_under_season"),
            "vs_opp": rates.get("hit_rate_under_vs_opp"),
            "vs_similar": rates.get("hit_rate_under_vs_similar"),
        }
    return {
        "l5": rates.get("hit_rate_over_l5"),
        "l10": rates.get("hit_rate_over_l10"),
        "season": rates.get("hit_rate_over_season"),
        "vs_opp": rates.get("hit_rate_over_vs_opp"),
        "vs_similar": rates.get("hit_rate_over_vs_similar"),
    }


def kickoff_profile(start_time_utc: str | None) -> dict[str, Any]:
    """TNF / early / afternoon / primetime window in America/New_York."""
    if not start_time_utc:
        return {"window": "unknown", "weekday": None, "hour_et": None, "multiplier": 1.0}
    raw = str(start_time_utc).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return {"window": "unknown", "weekday": None, "hour_et": None, "multiplier": 1.0}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ET)
    weekday = local.strftime("%A")
    hour = local.hour + local.minute / 60.0
    window = "afternoon"
    if weekday == "Thursday":
        window = "thursday_night"
    elif weekday == "Monday":
        window = "monday_night"
    elif weekday == "Sunday" and hour >= 19.0:
        window = "sunday_night"
    elif weekday == "Sunday" and hour < 14.0:
        window = "sunday_early"
    elif weekday in ("Saturday", "Friday"):
        window = "weekend_flex"
    elif hour >= 19.0:
        window = "primetime"
    # Primetime slightly more passing volume; TNF slightly flatter scoring.
    mult = 1.0
    if window in ("sunday_night", "monday_night", "primetime"):
        mult = 1.03
    elif window == "thursday_night":
        mult = 0.98
    elif window == "sunday_early":
        mult = 0.99
    return {
        "window": window,
        "weekday": weekday,
        "hour_et": round(hour, 2),
        "kickoff_et": local.strftime("%a %-I:%M %p ET") if False else local.strftime("%a %I:%M %p ET").lstrip("0"),
        "multiplier": mult,
    }


def kickoff_market_multiplier(market_type: str, profile: dict[str, Any]) -> float:
    base = float(profile.get("multiplier") or 1.0)
    if market_type.startswith("player_rush_"):
        # Rushing is closer to 1.0 in primetime; TNF grind slightly helps backs.
        if profile.get("window") == "thursday_night":
            return 1.02
        return 1.0 + (base - 1.0) * 0.35
    if market_type.startswith("player_pass_") or market_type in (
        "player_receptions",
        "player_reception_yds",
        "player_reception_longest",
    ):
        return base
    return 1.0 + (base - 1.0) * 0.5


def weather_profile(game: dict[str, Any]) -> dict[str, Any]:
    indoor = bool(game.get("indoor") or game.get("venue_indoor"))
    temp = game.get("weather_temp")
    wind = game.get("weather_wind_mph")
    condition = str(game.get("weather_condition") or game.get("weather_display") or "").strip()
    display = str(game.get("weather_display") or condition or "").strip()
    try:
        temp_f = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temp_f = None
    try:
        wind_mph = float(wind) if wind is not None else None
    except (TypeError, ValueError):
        wind_mph = None
    text = f"{display} {condition}".lower()
    wet = any(token in text for token in ("rain", "snow", "sleet", "shower", "storm"))
    windy = wind_mph is not None and wind_mph >= 15
    cold = temp_f is not None and temp_f <= 32 and not indoor
    if indoor:
        risk = "none"
        note = "Indoor / dome — weather not a factor"
    elif windy and wet:
        risk = "high"
        note = display or "Wind and precipitation"
    elif windy or wet or cold:
        risk = "moderate"
        note = display or ("Wind" if windy else "Cold" if cold else "Precipitation")
    else:
        risk = "none"
        note = display or None
    return {
        "indoor": indoor,
        "temp_f": temp_f,
        "wind_mph": wind_mph,
        "condition": display or None,
        "risk": risk,
        "note": note,
    }


def weather_market_multiplier(market_type: str, weather: dict[str, Any]) -> float:
    if weather.get("indoor") or weather.get("risk") == "none":
        return 1.0
    risk = weather.get("risk")
    windy = (weather.get("wind_mph") or 0) >= 15
    passing = market_type.startswith("player_pass_") or market_type in (
        "player_receptions",
        "player_reception_yds",
        "player_reception_longest",
        "player_pass_longest_completion",
    )
    rushing = market_type.startswith("player_rush_")
    mult = 1.0
    if passing:
        if risk == "high":
            mult *= 0.90
        elif risk == "moderate":
            mult *= 0.95
        if windy:
            mult *= 0.97
    elif rushing:
        if risk in ("high", "moderate"):
            mult *= 1.04
    return round(max(0.86, min(1.10, mult)), 4)


def defense_market_multiplier(market_type: str, defense: dict[str, Any] | None) -> float:
    """Scale expected volume from opponent yards allowed vs league average."""
    if not defense:
        return 1.0
    if market_type.startswith("player_pass_") or market_type in (
        "player_receptions",
        "player_reception_yds",
        "player_reception_longest",
    ):
        allowed = defense.get("pass_yds_allowed")
        league = LEAGUE_PASS_YDS_ALLOWED
    elif market_type.startswith("player_rush_"):
        allowed = defense.get("rush_yds_allowed")
        league = LEAGUE_RUSH_YDS_ALLOWED
    else:
        allowed = defense.get("points_allowed")
        league = LEAGUE_POINTS_ALLOWED
    try:
        allowed_f = float(allowed) if allowed is not None else None
    except (TypeError, ValueError):
        allowed_f = None
    if allowed_f is None or league <= 0:
        return 1.0
    raw = allowed_f / league
    return round(max(0.90, min(1.12, raw)), 4)


def cash_confidence(
    *,
    model_p: float | None,
    hit_l5: float | None = None,
    hit_l10: float | None,
    hit_vs_opp: float | None = None,
    hit_vs_similar: float | None = None,
    sample_games: int,
    injury_note: str | None,
    weather_risk: str | None,
    projection_confidence: str,
) -> dict[str, Any]:
    """Calibrated chance the recommended side cashes (percent)."""
    parts: list[tuple[float, float]] = []
    if model_p is not None:
        parts.append((float(model_p), 0.55))
    if hit_l10 is not None:
        parts.append((float(hit_l10), 0.22 if model_p is not None else 0.45))
    if hit_l5 is not None:
        parts.append((float(hit_l5), 0.12 if model_p is not None else 0.30))
    if hit_vs_opp is not None:
        parts.append((float(hit_vs_opp), 0.08))
    elif hit_vs_similar is not None:
        parts.append((float(hit_vs_similar), 0.06))
    if not parts:
        return {
            "confidence_pct": None,
            "confidence_label": "Insufficient data",
            "cash_probability": None,
        }
    total_w = sum(w for _, w in parts)
    raw = sum(v * w for v, w in parts) / total_w
    if sample_games < 3:
        raw *= 0.94
    elif sample_games < 5:
        raw *= 0.97
    if projection_confidence == "low":
        raw *= 0.98
    if injury_note:
        note = injury_note.upper()
        if "OUT" in note:
            raw *= 0.55
        elif "DOUBTFUL" in note:
            raw *= 0.80
        elif "QUESTIONABLE" in note:
            raw *= 0.92
        else:
            raw *= 0.95
    if weather_risk == "high":
        raw *= 0.95
    elif weather_risk == "moderate":
        raw *= 0.98
    cash_p = max(0.01, min(0.99, raw))
    pct = round(cash_p * 100.0, 1)
    if pct >= 70:
        label = "High"
    elif pct >= 58:
        label = "Medium"
    else:
        label = "Low"
    return {
        "confidence_pct": pct,
        "confidence_label": label,
        "cash_probability": round(cash_p, 4),
    }


def defense_label(defense: dict[str, Any] | None, market_type: str) -> str | None:
    if not defense:
        return None
    mult = defense_market_multiplier(market_type, defense)
    rank = defense.get("pass_rank") if "pass" in market_type or "reception" in market_type else defense.get("rush_rank")
    if "rush" in market_type:
        yards = defense.get("rush_yds_allowed")
        kind = "rush yards allowed"
    elif market_type.startswith("player_pass_") or "reception" in market_type:
        yards = defense.get("pass_yds_allowed")
        kind = "pass yards allowed"
    else:
        yards = defense.get("points_allowed")
        kind = "points allowed"
    softness = "soft" if mult >= 1.04 else "stout" if mult <= 0.96 else "average"
    bits = [f"Opponent {softness} ({kind}"]
    if yards is not None:
        bits[-1] = f"{bits[-1]} {yards:.1f}"
    bits[-1] += ")"
    if rank:
        bits.append(f"rank {rank}")
    return " · ".join(bits)
