"""Normalize UFC fighter names across ESPN and The Odds API."""

from __future__ import annotations

import re
import unicodedata

_UFC_ALIASES: dict[str, str] = {
    "conor mcgregor": "Conor McGregor",
    "max holloway": "Max Holloway",
    "alexander volkanovski": "Alex Volkanovski",
    "alex volkanovski": "Alex Volkanovski",
    "jon jones": "Jon Jones",
    "jonathan dwight jones": "Jon Jones",
    "islam makhachev": "Islam Makhachev",
    "charles oliveira": "Charles Oliveira",
    "ilia topuria": "Ilia Topuria",
    "daniel cormier": "Daniel Cormier",
    "stipe miocic": "Stipe Miocic",
    "amanda nunes": "Amanda Nunes",
    "valentina shevchenko": "Valentina Shevchenko",
    "khabib nurmagomedov": "Khabib Nurmagomedov",
    "jose aldo": "José Aldo",
    "gaston bolanos": "Gaston Bolaños",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _clean_raw(name: str) -> str:
    raw = " ".join(str(name).strip().split())
    raw = re.sub(r"\s+(jr\.?|sr\.?|iii|ii|iv)$", "", raw, flags=re.I)
    return raw


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
    if a_parts and b_parts and a_parts[-1] == b_parts[-1]:
        if len(a_parts) == 1 or len(b_parts) == 1:
            return True
        if a_parts[0] == b_parts[0]:
            return True
    return False
