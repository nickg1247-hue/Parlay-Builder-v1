"""Extra MLB context for player prop modals — splits, lineup, park, umpire."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.mlb_game_lineup import get_mlb_game_lineup
from app.services.prop_scoring import _http_client_get, lookup_pitcher_rates
from app.services.schedule_mlb import get_mlb_game

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"

PARK_HINTS: dict[str, str] = {
    "Coors Field": "Hitter-friendly",
    "Great American Ball Park": "Hitter-friendly",
    "Fenway Park": "Hitter-friendly",
    "Oracle Park": "Pitcher-friendly",
    "Petco Park": "Pitcher-friendly",
    "Oakland Coliseum": "Pitcher-friendly",
    "Tropicana Field": "Pitcher-friendly",
}


def _season_split_stats(player_id: int, group: str, sit_code: str, season: int) -> dict[str, Any]:
    """Fetch vsLeft/vsRight or home/away season splits."""
    url = f"{MLB_STATS_BASE}/people/{player_id}/stats"
    params = {
        "stats": "statSplits",
        "group": group,
        "season": season,
        "sitCodes": sit_code,
    }
    try:
        resp = _http_client_get().get(url, params=params)
        resp.raise_for_status()
        blocks = resp.json().get("stats") or []
        if not blocks:
            return {}
        splits = blocks[0].get("splits") or []
        return splits[0].get("stat") if splits else {}
    except Exception:
        return {}


def _game_meta(game_id: str, game_date: date) -> dict[str, Any]:
    detail = get_mlb_game(game_id, game_date)
    if not detail:
        return {}
    game = detail.get("game") or {}
    venue = game.get("venue") or {}
    venue_name = venue.get("name") or game.get("venue_name") or ""
    officials = game.get("officials") or []
    hp = next(
        (
            o.get("official", {}).get("fullName")
            for o in officials
            if isinstance(o, dict) and o.get("officialType") == "Home Plate"
        ),
        None,
    )
    return {
        "venue_name": venue_name,
        "park_hint": PARK_HINTS.get(venue_name),
        "home_plate_umpire": hp,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
    }


def _lineup_status(player_id: int, game_id: str, game_date: date) -> dict[str, Any]:
    lineup = get_mlb_game_lineup(game_id, game_date)
    if not lineup or lineup.get("status") == "unavailable":
        return {"in_lineup": None, "lineup_note": "Lineup not posted yet"}

    pid = str(player_id)
    for side in ("away", "home"):
        side_blob = lineup.get(side) or {}
        batters = side_blob.get("lineup") or []
        for b in batters:
            if str(b.get("id")) == pid:
                order = b.get("order") or b.get("batting_order")
                return {
                    "in_lineup": True,
                    "lineup_note": f"In {side} lineup{f' (#{order})' if order else ''}",
                    "batting_order": order,
                }
        sp = side_blob.get("starting_pitcher") or {}
        if str(sp.get("id")) == pid:
            return {"in_lineup": True, "lineup_note": f"Probable starter ({side})"}

    return {"in_lineup": False, "lineup_note": "Not in posted lineup"}


def _opposing_pitcher_context(
    player_id: int,
    game_id: str,
    game_date: date,
    *,
    is_pitcher: bool,
) -> dict[str, Any]:
    if is_pitcher:
        return {}
    lineup = get_mlb_game_lineup(game_id, game_date)
    if not lineup:
        return {}
    meta = _game_meta(game_id, game_date)
    # Determine player's team from lineup sides
    pid = str(player_id)
    player_side = None
    for side in ("away", "home"):
        side_blob = lineup.get(side) or {}
        for b in side_blob.get("lineup") or []:
            if str(b.get("id")) == pid:
                player_side = side
                break
        if player_side:
            break
    if not player_side:
        return {}
    opp_side = "home" if player_side == "away" else "away"
    sp = (lineup.get(opp_side) or {}).get("starting_pitcher") or {}
    name = sp.get("name") or sp.get("fullName")
    if not name:
        return {"opposing_pitcher": None}
    era, _ = lookup_pitcher_rates(name, game_date.year, {})
    out: dict[str, Any] = {"opposing_pitcher": name}
    if era is not None:
        out["opposing_pitcher_era"] = round(float(era), 2)
    return out


def get_mlb_player_depth(
    player_id: int,
    *,
    game_id: str | None = None,
    market_type: str = "",
    season: int | None = None,
) -> dict[str, Any]:
    """Badges and split rows for prop modal."""
    yr = season or date.today().year
    gd = date.today()
    is_pitcher = market_type.startswith("pitcher_")

    person: dict[str, Any] = {}
    try:
        resp = _http_client_get().get(f"{MLB_STATS_BASE}/people/{player_id}")
        people = resp.json().get("people") or []
        person = people[0] if people else {}
    except Exception:
        pass

    bats = (person.get("batSide") or {}).get("code") or ""
    throws = (person.get("pitchHand") or {}).get("code") or ""

    splits: dict[str, Any] = {}
    if not is_pitcher and bats:
        sit = "vl" if bats.upper() == "R" else "vr" if bats.upper() == "L" else ""
        if sit:
            stat = _season_split_stats(player_id, "hitting", sit, yr)
            if stat:
                splits["platoon"] = {
                    "label": "vs LHP" if sit == "vl" else "vs RHP",
                    "avg": stat.get("avg"),
                    "ops": stat.get("ops"),
                    "homeRuns": stat.get("homeRuns"),
                    "atBats": stat.get("atBats"),
                }

    badges: list[dict[str, str]] = []
    lineup_info: dict[str, Any] = {}
    game_meta: dict[str, Any] = {}
    pitcher_ctx: dict[str, Any] = {}

    if game_id:
        game_meta = _game_meta(game_id, gd)
        lineup_info = _lineup_status(player_id, game_id, gd)
        pitcher_ctx = _opposing_pitcher_context(
            player_id, game_id, gd, is_pitcher=is_pitcher
        )
        if game_meta.get("park_hint"):
            badges.append({"type": "park", "label": game_meta["park_hint"]})
        if game_meta.get("home_plate_umpire"):
            badges.append({"type": "umpire", "label": f"HP: {game_meta['home_plate_umpire']}"})
        if lineup_info.get("lineup_note"):
            badges.append({"type": "lineup", "label": lineup_info["lineup_note"]})
        era = pitcher_ctx.get("opposing_pitcher_era")
        if era is not None:
            if era >= 4.5:
                badges.append({"type": "matchup", "label": f"Soft SP ({era} ERA)"})
            elif era <= 3.2:
                badges.append({"type": "matchup", "label": f"Tough SP ({era} ERA)"})

    return {
        "bats": bats,
        "throws": throws,
        "splits": splits,
        "badges": badges,
        "lineup": lineup_info,
        "game": game_meta,
        "opposing_pitcher": pitcher_ctx.get("opposing_pitcher"),
        "opposing_pitcher_era": pitcher_ctx.get("opposing_pitcher_era"),
    }
