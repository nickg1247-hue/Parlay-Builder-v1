"""UFC fighter headshots and country flags from ESPN."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.odds.ufc_fighter_aliases import fighter_match_key, normalize_fighter_name

logger = logging.getLogger(__name__)

HEADSHOT_URL = "https://a.espncdn.com/i/headshots/mma/players/full/{athlete_id}.png"
FLAG_BACKDROP_URL = "https://flagcdn.com/w1280/{iso}.png"
_MEDIA_CACHE: dict[str, tuple[datetime, dict[str, dict[str, Any]]]] = {}
_MEDIA_CACHE_TTL = timedelta(hours=6)

# ESPN country slug (teamlogos path) -> ISO 3166-1 alpha-2 for flagcdn backdrops.
_ESPN_COUNTRY_TO_ISO: dict[str, str] = {
    "usa": "us",
    "irl": "ie",
    "gbr": "gb",
    "eng": "gb-eng",
    "sco": "gb-sct",
    "wls": "gb-wls",
    "bra": "br",
    "mex": "mx",
    "can": "ca",
    "aus": "au",
    "nzl": "nz",
    "rus": "ru",
    "chn": "cn",
    "jpn": "jp",
    "kor": "kr",
    "pol": "pl",
    "fra": "fr",
    "deu": "de",
    "ger": "de",
    "swe": "se",
    "nor": "no",
    "fin": "fi",
    "ned": "nl",
    "nld": "nl",
    "ita": "it",
    "esp": "es",
    "arg": "ar",
    "col": "co",
    "per": "pe",
    "chl": "cl",
    "chi": "cl",
    "ven": "ve",
    "ecu": "ec",
    "uru": "uy",
    "ury": "uy",
    "par": "py",
    "bol": "bo",
    "cub": "cu",
    "dom": "do",
    "jam": "jm",
    "pur": "pr",
    "phi": "ph",
    "phl": "ph",
    "tha": "th",
    "vnm": "vn",
    "vie": "vn",
    "ind": "in",
    "pak": "pk",
    "afg": "af",
    "kaz": "kz",
    "uzb": "uz",
    "geo": "ge",
    "arm": "am",
    "aze": "az",
    "tur": "tr",
    "gre": "gr",
    "grc": "gr",
    "cro": "hr",
    "srb": "rs",
    "bih": "ba",
    "rou": "ro",
    "rom": "ro",
    "bul": "bg",
    "hun": "hu",
    "cze": "cz",
    "svk": "sk",
    "ukr": "ua",
    "bel": "be",
    "aut": "at",
    "che": "ch",
    "sui": "ch",
    "den": "dk",
    "dnk": "dk",
    "isl": "is",
    "egy": "eg",
    "nga": "ng",
    "rsa": "za",
    "zaf": "za",
    "mar": "ma",
    "alg": "dz",
    "tun": "tn",
    "sen": "sn",
    "cmr": "cm",
    "gha": "gh",
    "ken": "ke",
    "tpe": "tw",
    "twn": "tw",
    "hon": "hn",
    "crc": "cr",
    "pan": "pa",
    "nic": "ni",
    "gua": "gt",
    "sal": "sv",
    "slv": "sv",
}

_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "usa": "us",
    "united states": "us",
    "ireland": "ie",
    "brazil": "br",
    "mexico": "mx",
    "canada": "ca",
    "australia": "au",
    "new zealand": "nz",
    "russia": "ru",
    "china": "cn",
    "japan": "jp",
    "south korea": "kr",
    "poland": "pl",
    "france": "fr",
    "germany": "de",
    "sweden": "se",
    "norway": "no",
    "finland": "fi",
    "netherlands": "nl",
    "italy": "it",
    "spain": "es",
    "argentina": "ar",
    "colombia": "co",
    "england": "gb-eng",
    "scotland": "gb-sct",
    "wales": "gb-wls",
    "united kingdom": "gb",
    "uk": "gb",
}


def _iso_from_espn_flag(flag_href: str | None) -> str | None:
    if not flag_href:
        return None
    match = re.search(r"/countries/\d+/([a-z]{2,4})\.png", flag_href.lower())
    if not match:
        return None
    slug = match.group(1)
    return _ESPN_COUNTRY_TO_ISO.get(slug, slug if len(slug) == 2 else None)


def _iso_from_country_name(country: str | None) -> str | None:
    if not country:
        return None
    return _COUNTRY_NAME_TO_ISO.get(country.strip().lower())


def country_code(flag_href: str | None, country: str | None = None) -> str | None:
    """ISO-style country code for UI (e.g. US, IE, BR)."""
    iso = _iso_from_espn_flag(flag_href) or _iso_from_country_name(country)
    if not iso:
        return None
    part = iso.split("-")[0]
    if len(part) == 2:
        return part.upper()
    return iso.upper()


def flag_backdrop_url(flag_href: str | None, country: str | None = None) -> str | None:
    """High-res rectangular flag for full-bleed fight poster backgrounds."""
    iso = _iso_from_espn_flag(flag_href) or _iso_from_country_name(country)
    if not iso:
        return None
    return FLAG_BACKDROP_URL.format(iso=iso)


def headshot_url(athlete_id: str | int | None) -> str | None:
    if athlete_id is None:
        return None
    aid = str(athlete_id).strip()
    if not aid.isdigit():
        return None
    return HEADSHOT_URL.format(athlete_id=aid)


def media_from_competitor(competitor: dict[str, Any]) -> dict[str, Any] | None:
    athlete = competitor.get("athlete") or {}
    name = normalize_fighter_name(
        athlete.get("displayName") or athlete.get("shortName") or ""
    )
    if not name:
        return None
    athlete_id = competitor.get("id")
    flag = athlete.get("flag") or {}
    flag_url = flag.get("href")
    country = flag.get("alt") or flag.get("country")
    return {
        "name": name,
        "athlete_id": str(athlete_id) if athlete_id is not None else None,
        "headshot_url": headshot_url(athlete_id),
        "flag_url": flag_url,
        "flag_backdrop_url": flag_backdrop_url(flag_url, country),
        "country_code": country_code(flag_url, country),
        "country": country,
    }


def media_map_for_date(game_date: date, *, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Normalized fighter name -> headshot / flag metadata for a card date."""
    cache_key = game_date.isoformat()
    now = datetime.now(timezone.utc)
    if not force_refresh and cache_key in _MEDIA_CACHE:
        cached_at, payload = _MEDIA_CACHE[cache_key]
        if now - cached_at < _MEDIA_CACHE_TTL:
            return payload

    out: dict[str, dict[str, Any]] = {}
    try:
        from app.services.scores_ufc import fetch_ufc_scoreboard_day

        events = fetch_ufc_scoreboard_day(game_date)
    except Exception as exc:
        logger.warning("UFC fighter media fetch failed for %s: %s", cache_key, exc)
        return out

    for event in events:
        for comp in event.get("competitions") or []:
            for competitor in comp.get("competitors") or []:
                row = media_from_competitor(competitor)
                if not row:
                    continue
                key = fighter_match_key(row["name"])
                out[key] = row
    _MEDIA_CACHE[cache_key] = (now, out)
    return out


def _corner_media(
    fighter_name: str,
    media_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = fighter_match_key(fighter_name)
    row = media_map.get(key)
    if row:
        return row
    for media in media_map.values():
        if fighter_match_key(media.get("name", "")) == key:
            return media
    return {
        "name": fighter_name,
        "athlete_id": None,
        "headshot_url": None,
        "flag_url": None,
        "flag_backdrop_url": None,
        "country_code": None,
        "country": None,
    }


def lookup_fighter_media(
    fighter_name: str,
    *,
    prefer_date: date | None = None,
) -> dict[str, Any]:
    """Best-effort headshot / flag for a fighter name across nearby card dates."""
    dates_to_try: list[date] = []
    if prefer_date:
        dates_to_try.append(prefer_date)
    try:
        from app.services.schedule_ufc import get_ufc_schedule

        schedule = get_ufc_schedule(None, auto_resolve=True)
        resolved = schedule.get("resolved_date") or schedule.get("date")
        if resolved:
            card_day = date.fromisoformat(str(resolved)[:10])
            if card_day not in dates_to_try:
                dates_to_try.append(card_day)
    except Exception:
        pass
    today = date.today()
    if today not in dates_to_try:
        dates_to_try.append(today)

    for game_date in dates_to_try:
        media_map = media_map_for_date(game_date)
        row = _corner_media(fighter_name, media_map)
        if row.get("headshot_url"):
            return row
    return _corner_media(fighter_name, {})


def enrich_fight_media(fight: dict[str, Any], game_date: date) -> dict[str, Any]:
    """Attach headshot + flag URLs to a fight dict (mutates copy)."""
    enriched = dict(fight)
    media_map = media_map_for_date(game_date)
    if not media_map:
        return enriched

    home_name = enriched.get("home_team") or enriched.get("home_fighter") or ""
    away_name = enriched.get("away_team") or enriched.get("away_fighter") or ""
    home = _corner_media(home_name, media_map)
    away = _corner_media(away_name, media_map)

    enriched["home_athlete_id"] = home.get("athlete_id")
    enriched["away_athlete_id"] = away.get("athlete_id")
    enriched["home_headshot_url"] = home.get("headshot_url")
    enriched["away_headshot_url"] = away.get("headshot_url")
    enriched["home_flag_url"] = home.get("flag_url")
    enriched["away_flag_url"] = away.get("flag_url")
    enriched["home_flag_backdrop_url"] = home.get("flag_backdrop_url")
    enriched["away_flag_backdrop_url"] = away.get("flag_backdrop_url")
    enriched["home_country"] = home.get("country")
    enriched["away_country"] = away.get("country")
    enriched["home_country_code"] = home.get("country_code")
    enriched["away_country_code"] = away.get("country_code")
    # Reuse team logo fields consumed by shared header helpers.
    enriched["home_logo_url"] = home.get("headshot_url")
    enriched["away_logo_url"] = away.get("headshot_url")
    return enriched
