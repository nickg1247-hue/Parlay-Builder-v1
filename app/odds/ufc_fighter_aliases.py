"""Normalize UFC fighter names across ESPN and The Odds API."""

from __future__ import annotations

import re
import unicodedata

_UFC_ALIASES: dict[str, str] = {
    "conor mcgregor": "Conor McGregor",
    "the notorious conor mcgregor": "Conor McGregor",
    "max holloway": "Max Holloway",
    "blessed max holloway": "Max Holloway",
    "alexander volkanovski": "Alex Volkanovski",
    "alex volkanovski": "Alex Volkanovski",
    "jon jones": "Jon Jones",
    "jonathan dwight jones": "Jon Jones",
    "islam makhachev": "Islam Makhachev",
    "charles oliveira": "Charles Oliveira",
    "do bronx charles oliveira": "Charles Oliveira",
    "ilia topuria": "Ilia Topuria",
    "daniel cormier": "Daniel Cormier",
    "dc daniel cormier": "Daniel Cormier",
    "stipe miocic": "Stipe Miocic",
    "amanda nunes": "Amanda Nunes",
    "lioness amanda nunes": "Amanda Nunes",
    "valentina shevchenko": "Valentina Shevchenko",
    "khabib nurmagomedov": "Khabib Nurmagomedov",
    "jose aldo": "José Aldo",
    "jose aldo jr": "José Aldo",
    "gaston bolanos": "Gaston Bolaños",
    "gaston bolanos": "Gaston Bolaños",
    "magomed ankalaev": "Magomed Ankalaev",
    "johnny walker": "Johnny Walker",
    "jim miller": "Jim Miller",
    "gabriel benitez": "Gabriel Benitez",
    "mario bautista": "Mario Bautista",
    "ricky simon": "Ricky Simon",
    "waldo cortes-acosta": "Waldo Cortes Acosta",
    "waldo cortes acosta": "Waldo Cortes Acosta",
    "brunno ferreira": "Brunno Ferreira",
    "phil hawes": "Phil Hawes",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _clean_raw(name: str) -> str:
    raw = " ".join(str(name).strip().split())
    raw = re.sub(r'^["\'][^"\']+["\']\s+', "", raw)
    raw = re.sub(r"\s+(jr\.?|sr\.?|iii|ii|iv)$", "", raw, flags=re.I)
    if "," in raw and not raw.startswith('"'):
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            raw = f"{parts[1]} {parts[0]}"
    raw = re.sub(r'^["\']|["\']$', "", raw)
    raw = re.sub(r"\s+\(.+\)$", "", raw)
    raw = re.sub(r"^(the|el|la)\s+", "", raw, flags=re.I)
    return raw.strip()


def normalize_fighter_name(name: str) -> str:
    """Map ESPN / Odds API name variants to a canonical display name."""
    if not name or not str(name).strip():
        return name
    raw = _clean_raw(name)
    key = _strip_accents(raw).lower()
    if key in _UFC_ALIASES:
        return _UFC_ALIASES[key]
    return raw


def fighter_match_key(name: str) -> str:
    """Loose key for crosswalk when punctuation differs."""
    base = normalize_fighter_name(name)
    base = _strip_accents(base).lower()
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return " ".join(base.split())


def fighter_slug(name: str) -> str:
    """URL slug for fighter profile pages."""
    key = fighter_match_key(name)
    return key.replace(" ", "-")


def fighters_match(name_a: str, name_b: str) -> bool:
    """True when two display names likely refer to the same fighter."""
    a = fighter_match_key(name_a)
    b = fighter_match_key(name_b)
    if not a or not b:
        return False
    if a == b:
        return True
    a_parts = a.split()
    b_parts = b.split()
    if not a_parts or not b_parts:
        return False
    if a_parts[-1] == b_parts[-1]:
        if len(a_parts) == 1 or len(b_parts) == 1:
            return True
        if a_parts[0] == b_parts[0]:
            return True
        if len(a_parts[0]) == 1 and a_parts[0] == b_parts[0][0]:
            return True
        if len(b_parts[0]) == 1 and b_parts[0] == a_parts[0][0]:
            return True
    if len(a_parts) >= 2 and len(b_parts) >= 2:
        if a_parts[-1] == b_parts[-1] and a_parts[-2] == b_parts[-2]:
            return True
    return False
