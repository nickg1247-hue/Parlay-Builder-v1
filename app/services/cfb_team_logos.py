"""ESPN NCAA team logos — FBS-focused map keyed for CFBD / ingest school names."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.odds.cfb_team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

ESPN_CFB_TEAMS = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
)
LOGO_MAP_PATH = PROJECT_ROOT / "data" / "processed" / "cfb_team_logos.json"
ESPN_LOGO_TEMPLATE = "https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"
MAP_VERSION = 2


def _team_meta(block: dict[str, Any]) -> dict[str, Any] | None:
    team_id = block.get("id")
    if team_id is None:
        return None
    logo = block.get("logo") or ESPN_LOGO_TEMPLATE.format(team_id=team_id)
    return {
        "team_id": int(team_id),
        "abbreviation": block.get("abbreviation"),
        "display_name": block.get("displayName") or block.get("name"),
        "logo_url": logo,
    }


def _alias_keys(block: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("displayName", "shortDisplayName", "location", "nickname", "name"):
        val = block.get(field)
        if val:
            keys.add(normalize_team_name(str(val)))
    abbr = block.get("abbreviation")
    if abbr:
        keys.add(normalize_team_name(str(abbr)))
    return {k for k in keys if k}


def _build_logo_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map normalized school names (and aliases) to logo metadata."""
    lookup: dict[str, dict[str, Any]] = {}
    for sport in payload.get("sports") or []:
        for league in sport.get("leagues") or []:
            for team in league.get("teams") or []:
                block = team.get("team") or team
                meta = _team_meta(block)
                if meta is None:
                    continue
                for key in _alias_keys(block):
                    lookup[key] = meta
    return lookup


def _fetch_espn_teams(*, groups: str = "80") -> dict[str, Any]:
    """Paginate ESPN college-football teams (FBS group 80 includes related schools)."""
    merged: dict[str, Any] = {"sports": [{"leagues": [{"teams": []}]}]}
    teams_out: list[dict[str, Any]] = merged["sports"][0]["leagues"][0]["teams"]
    with httpx.Client(timeout=60.0) as client:
        page = 1
        while page <= 10:
            response = client.get(
                ESPN_CFB_TEAMS,
                params={"groups": groups, "limit": "500", "page": str(page)},
            )
            response.raise_for_status()
            payload = response.json()
            page_teams: list[dict[str, Any]] = []
            for sport in payload.get("sports") or []:
                for league in sport.get("leagues") or []:
                    page_teams.extend(league.get("teams") or [])
            if not page_teams:
                break
            teams_out.extend(page_teams)
            page += 1
    return merged


def refresh_cfb_logo_map(*, force: bool = False, groups: str = "80") -> dict[str, dict[str, Any]]:
    """Fetch ESPN teams and write cached logo lookup (run from bootstrap or script)."""
    load_cfb_logo_map.cache_clear()
    if force and LOGO_MAP_PATH.exists():
        LOGO_MAP_PATH.unlink(missing_ok=True)
    payload = _fetch_espn_teams(groups=groups)
    lookup = _build_logo_map(payload)
    LOGO_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGO_MAP_PATH.write_text(
        json.dumps(
            {
                "version": MAP_VERSION,
                "groups": groups,
                "team_count": len(lookup),
                "lookup": lookup,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Cached %d CFB logo lookup keys", len(lookup))
    return lookup


def _read_logo_lookup() -> dict[str, dict[str, Any]]:
    if not LOGO_MAP_PATH.exists():
        return {}
    try:
        raw = json.loads(LOGO_MAP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(raw, dict) and "lookup" in raw:
        lookup = raw.get("lookup") or {}
        return lookup if isinstance(lookup, dict) else {}
    if isinstance(raw, dict) and raw:
        # Legacy v1: displayName keys only
        return raw
    return {}


@lru_cache(maxsize=1)
def load_cfb_logo_map() -> dict[str, dict[str, Any]]:
    lookup = _read_logo_lookup()
    if lookup:
        return lookup
    try:
        return refresh_cfb_logo_map()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("CFB logo map fetch failed: %s", exc)
        return {}


def lookup_team_logo(team_name: str) -> dict[str, Any] | None:
    if not team_name:
        return None
    logo_map = load_cfb_logo_map()
    key = normalize_team_name(str(team_name))
    return logo_map.get(key)


def enrich_game_logos(game: dict[str, Any]) -> dict[str, Any]:
    """Fill missing logo URLs / team ids from cached ESPN team map."""
    out = dict(game)
    for side in ("home", "away"):
        team_key = f"{side}_team"
        logo_key = f"{side}_logo_url"
        id_key = f"{side}_team_id"
        abbr_key = f"{side}_team_abbr"
        if out.get(logo_key):
            continue
        meta = lookup_team_logo(str(out.get(team_key) or ""))
        if not meta:
            continue
        if meta.get("logo_url"):
            out[logo_key] = meta["logo_url"]
        if out.get(id_key) is None and meta.get("team_id") is not None:
            out[id_key] = meta["team_id"]
        if not out.get(abbr_key) and meta.get("abbreviation"):
            out[abbr_key] = meta["abbreviation"]
    return out


def enrich_games_logos(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_game_logos(g) for g in games]
