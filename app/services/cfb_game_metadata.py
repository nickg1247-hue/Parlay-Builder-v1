"""Shared division, conference, ranking, and HBCU metadata for CFB slates."""

from __future__ import annotations

import re
from typing import Any

from app.odds.cfb_team_aliases import normalize_team_name

DIVISION_LABELS = {"fbs": "FBS", "fcs": "FCS"}
DIVISION_ORDER = ("fbs", "fcs")
FCS_CONFERENCES = frozenset({"big sky","big south-ovc","caa","ivy league","meac","mvfc","nec","ovc","patriot league","pioneer football league","socon","southland","swac","uac"})
KNOWN_LOWER_DIVISION_TEAM_KEYS = frozenset({"west florida","northwestern (ia)","la. christian"})

def is_fcs_conference(value: Any) -> bool:
    return str(value or "").strip().lower() in FCS_CONFERENCES

def is_public_fbs_fcs_game(game: dict[str, Any]) -> bool:
    divisions=set(game.get("divisions") or [game.get("division")])
    if not divisions or not divisions <= {"fbs","fcs"}: return False
    if divisions=={"fcs"}:
        teams={_school_key(game.get("home_team")),_school_key(game.get("away_team"))}
        return not bool(teams & KNOWN_LOWER_DIVISION_TEAM_KEYS) and is_fcs_conference(game.get("home_conference")) and is_fcs_conference(game.get("away_conference"))
    return True

HBCU_CONFERENCES = frozenset(
    {
        "central intercollegiate athletic association",
        "ciaa",
        "mid-eastern athletic conference",
        "meac",
        "southern intercollegiate athletic conference",
        "siac",
        "southwestern athletic conference",
        "swac",
    }
)

# NCAA football-playing HBCUs outside the four HBCU conferences are included too.
HBCU_TEAMS = frozenset(
    {
        "alabama a&m",
        "alabama state",
        "albany state",
        "alcorn state",
        "allen",
        "arkansas-pine bluff",
        "benedict",
        "bethune-cookman",
        "bluefield state",
        "bowie state",
        "central state",
        "clark atlanta",
        "delaware state",
        "edward waters",
        "elizabeth city state",
        "fayetteville state",
        "florida a&m",
        "fort valley state",
        "grambling state",
        "hampton",
        "howard",
        "jackson state",
        "johnson c. smith",
        "kentucky state",
        "lane",
        "lincoln (mo)",
        "lincoln (pa)",
        "livingstone",
        "miles",
        "mississippi valley state",
        "morehouse",
        "morgan state",
        "norfolk state",
        "north carolina a&t",
        "north carolina central",
        "prairie view a&m",
        "savannah state",
        "shaw",
        "south carolina state",
        "southern",
        "tennessee state",
        "texas southern",
        "tuskegee",
        "virginia state",
        "virginia union",
        "west virginia state",
        "winston-salem state",
    }
)


def normalize_division(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("division", "").replace(" ", "")
    aliases = {
        "1a": "fbs",
        "ia": "fbs",
        "fbs": "fbs",
        "1aa": "fcs",
        "iaa": "fcs",
        "fcs": "fcs",
        "2": "d2",
        "ii": "d2",
        "d2": "d2",
        "3": "d3",
        "iii": "d3",
        "d3": "d3",
    }
    return aliases.get(raw, raw if raw in DIVISION_LABELS else "")


def _school_key(name: Any) -> str:
    normalized = normalize_team_name(str(name or "")).lower().strip()
    normalized = re.sub(r"\bst\.?$", "state", normalized)
    for suffix in (
        " wildcats",
        " bulldogs",
        " tigers",
        " panthers",
        " bears",
        " braves",
        " hornets",
        " rams",
        " lions",
        " eagles",
        " spartans",
        " pirates",
        " bison",
        " aggies",
        " jaguars",
        " penguins",
        " mountaineers",
        " redhawks",
        " governors",
        " paladins",
    ):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def is_hbcu_team(team: Any, conference: Any = "") -> bool:
    conf_key = str(conference or "").strip().lower()
    if conf_key in HBCU_CONFERENCES:
        return True
    school = _school_key(team)
    return school in HBCU_TEAMS


def annotate_game_metadata(game: dict[str, Any]) -> dict[str, Any]:
    divisions: list[str] = []
    for value in game.get("divisions") or []:
        key = normalize_division(value)
        if key and key not in divisions:
            divisions.append(key)
    for side in ("home", "away"):
        key = normalize_division(game.get(f"{side}_division"))
        if key and key not in divisions:
            divisions.append(key)
    primary = normalize_division(game.get("division"))
    if primary and primary not in divisions:
        divisions.append(primary)
    divisions.sort(key=lambda key: DIVISION_ORDER.index(key) if key in DIVISION_ORDER else 99)
    if not primary:
        primary = divisions[0] if divisions else "fbs"
    game["division"] = primary
    game["division_label"] = DIVISION_LABELS.get(primary, primary.upper())
    game["divisions"] = divisions or [primary]
    pure_fbs = game["divisions"] == ["fbs"]
    pure_fcs = game["divisions"] == ["fcs"]
    game["model_eligible"] = pure_fbs
    game["model_family"] = "cfb_moneyline" if pure_fbs else ("fcs_moneyline" if pure_fcs else None)
    game["model_version"] = None if not pure_fbs else "active_fbs"
    game.pop("is_hbcu", None)
    return game


def game_identity(game: dict[str, Any]) -> tuple[str, tuple[str, str]]:
    day = str(game.get("start_time_utc") or game.get("date") or "")[:10]
    teams = tuple(
        sorted(
            (
                _school_key(game.get("home_team_model_name") or game.get("home_team")),
                _school_key(game.get("away_team_model_name") or game.get("away_team")),
            )
        )
    )
    return day, teams


def merge_game_records(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate cross-division listings while preserving richer metadata."""
    merged: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    order: list[tuple[str, tuple[str, str]]] = []
    for raw in games:
        incoming = annotate_game_metadata(dict(raw))
        key = game_identity(incoming)
        if key not in merged:
            merged[key] = incoming
            order.append(key)
            continue
        current = merged[key]
        for field in (
            "home_conference",
            "away_conference",
            "home_rank",
            "away_rank",
            "home_logo_url",
            "away_logo_url",
            "home_team_id",
            "away_team_id",
            "home_record",
            "away_record",
            "network",
            "neutral_site",
            "neutral_site_known",
            "neutral_site_missing",
            "neutral_site_source",
        ):
            if current.get(field) in (None, "") and incoming.get(field) not in (None, ""):
                current[field] = incoming[field]
        current["divisions"] = list(
            dict.fromkeys((current.get("divisions") or []) + (incoming.get("divisions") or []))
        )
        current["sources"] = list(
            dict.fromkeys(
                (current.get("sources") or [current.get("source")])
                + (incoming.get("sources") or [incoming.get("source")])
            )
        )
        annotate_game_metadata(current)
    return [merged[key] for key in order]
