"""Normalize NFL team names for Odds API ↔ ESPN matching."""

from __future__ import annotations

from app.ingest.nfl import normalize_abbr

_NAME_TO_ABBR = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "oakland raiders": "LV",
    "los angeles chargers": "LAC",
    "san diego chargers": "LAC",
    "los angeles rams": "LAR",
    "st louis rams": "LAR",
    "st. louis rams": "LAR",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WSH",
    "washington football team": "WSH",
    "washington redskins": "WSH",
    "washington": "WSH",
}


def normalize_nfl_team(name: str | None) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    abbr = normalize_abbr(raw)
    if len(abbr) <= 3 and abbr.isalpha():
        return abbr
    key = raw.lower().replace(".", "")
    mapped = _NAME_TO_ABBR.get(key)
    if mapped:
        return mapped
    # Last token fallback: "Chiefs" won't uniquely map; require full name.
    return normalize_abbr(raw)
