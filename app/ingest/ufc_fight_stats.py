"""Per-bout UFC fight stats from ESPN Core competition statistics."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.ufc_fighter_aliases import normalize_fighter_name

logger = logging.getLogger(__name__)

ESPN_UFC_COMPETITION = (
    "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events"
    "/{event_id}/competitions/{fight_id}"
)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "ufc_fight_stats.parquet"
REQUEST_SLEEP_SECONDS = 0.15
REQUEST_RETRIES = 4

STAT_NAMES = (
    "sigStrikesLanded",
    "sigStrikesAttempted",
    "takedownsLanded",
    "takedownsAttempted",
    "timeInControl",
)


@dataclass
class CompetitorFightStats:
    fighter: str
    side: str  # home | away
    sig_strikes_landed: float | None = None
    sig_strikes_attempted: float | None = None
    takedowns_landed: float | None = None
    takedowns_attempted: float | None = None
    control_seconds: float | None = None


def _stat_map(stats_payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    splits = stats_payload.get("splits") or {}
    for category in splits.get("categories") or []:
        for stat in category.get("stats") or []:
            name = stat.get("name")
            if name in STAT_NAMES and stat.get("value") is not None:
                out[name] = float(stat["value"])
    return out


def _competitor_stats(
    client: httpx.Client,
    stats_ref: str | None,
    athlete_ref: str | None,
    athletes: dict[str, str],
) -> dict[str, float]:
    if not stats_ref:
        return {}
    for attempt in range(REQUEST_RETRIES):
        try:
            payload = client.get(stats_ref, timeout=30.0).json()
            if "$ref" in payload and "splits" not in payload:
                payload = client.get(payload["$ref"], timeout=30.0).json()
            return _stat_map(payload)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            if attempt + 1 >= REQUEST_RETRIES:
                logger.debug("Stats fetch failed %s: %s", stats_ref, exc)
                return {}
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
    return {}


def _resolve_athlete_name(
    client: httpx.Client,
    athlete_ref: str | None,
    cache: dict[str, str],
) -> str:
    if not athlete_ref:
        return ""
    if athlete_ref in cache:
        return cache[athlete_ref]
    for attempt in range(REQUEST_RETRIES):
        try:
            data = client.get(athlete_ref, timeout=30.0).json()
            name = normalize_fighter_name(
                str(data.get("displayName") or data.get("fullName") or "")
            )
            cache[athlete_ref] = name
            time.sleep(REQUEST_SLEEP_SECONDS)
            return name
        except (httpx.HTTPError, ValueError) as exc:
            if attempt + 1 >= REQUEST_RETRIES:
                logger.debug("Athlete fetch failed %s: %s", athlete_ref, exc)
                return ""
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
    return ""


def _parse_competition(
    client: httpx.Client,
    comp: dict[str, Any],
    athletes: dict[str, str],
) -> list[CompetitorFightStats]:
    competitors = comp.get("competitors") or []
    parsed: list[CompetitorFightStats] = []
    for comp_entry in competitors:
        if isinstance(comp_entry, dict) and "$ref" in comp_entry and "order" not in comp_entry:
            try:
                comp_entry = client.get(comp_entry["$ref"], timeout=30.0).json()
                time.sleep(REQUEST_SLEEP_SECONDS)
            except (httpx.HTTPError, ValueError):
                continue
        order = int(comp_entry.get("order") or 0)
        side = "home" if order == 1 else "away"
        athlete_ref = (comp_entry.get("athlete") or {}).get("$ref")
        name = _resolve_athlete_name(client, athlete_ref, athletes)
        stats_ref = (comp_entry.get("statistics") or {}).get("$ref")
        smap = _competitor_stats(client, stats_ref, athlete_ref, athletes)
        if not name:
            continue
        parsed.append(
            CompetitorFightStats(
                fighter=name,
                side=side,
                sig_strikes_landed=smap.get("sigStrikesLanded"),
                sig_strikes_attempted=smap.get("sigStrikesAttempted"),
                takedowns_landed=smap.get("takedownsLanded"),
                takedowns_attempted=smap.get("takedownsAttempted"),
                control_seconds=smap.get("timeInControl"),
            )
        )
    return parsed


def fetch_fight_stats_row(
    client: httpx.Client,
    *,
    event_id: str,
    fight_id: str,
    date: str,
    season: int,
    home_team: str,
    away_team: str,
    athletes: dict[str, str],
) -> dict[str, Any] | None:
    url = ESPN_UFC_COMPETITION.format(event_id=event_id, fight_id=fight_id)
    for attempt in range(REQUEST_RETRIES):
        try:
            comp = client.get(url, timeout=60.0).json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            if attempt + 1 >= REQUEST_RETRIES:
                logger.warning("Competition fetch failed %s: %s", fight_id, exc)
                return None
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
    else:
        return None

    stats = _parse_competition(client, comp, athletes)
    if not stats:
        return None

    by_side = {s.side: s for s in stats}
    home = by_side.get("home")
    away = by_side.get("away")
    if home is None and away is None:
        return None

    def _val(side_stats: CompetitorFightStats | None, attr: str) -> float | None:
        if side_stats is None:
            return None
        return getattr(side_stats, attr)

    return {
        "fight_id": str(fight_id),
        "event_id": str(event_id),
        "date": date,
        "season": int(season),
        "home_team": normalize_fighter_name(home_team),
        "away_team": normalize_fighter_name(away_team),
        "home_sig_strikes_landed": _val(home, "sig_strikes_landed"),
        "away_sig_strikes_landed": _val(away, "sig_strikes_landed"),
        "home_sig_strikes_attempted": _val(home, "sig_strikes_attempted"),
        "away_sig_strikes_attempted": _val(away, "sig_strikes_attempted"),
        "home_takedowns_landed": _val(home, "takedowns_landed"),
        "away_takedowns_landed": _val(away, "takedowns_landed"),
        "home_takedowns_attempted": _val(home, "takedowns_attempted"),
        "away_takedowns_attempted": _val(away, "takedowns_attempted"),
        "home_control_seconds": _val(home, "control_seconds"),
        "away_control_seconds": _val(away, "control_seconds"),
    }


def load_fight_stats() -> pd.DataFrame:
    if not PROCESSED_PARQUET.exists():
        return pd.DataFrame()
    df = pd.read_parquet(PROCESSED_PARQUET)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "fight_id"]).reset_index(drop=True)


def run_ingest(
    fights_df: pd.DataFrame | None = None,
    *,
    limit: int | None = None,
    skip_existing: bool = True,
) -> pd.DataFrame:
    """Fetch ESPN per-bout stats for fights in ufc_fights.parquet."""
    if fights_df is None:
        from app.models.ufc_baseline import load_fights

        fights_df = load_fights()

    existing_ids: set[str] = set()
    if skip_existing and PROCESSED_PARQUET.exists():
        existing_ids = set(load_fight_stats()["fight_id"].astype(str))

    rows: list[dict[str, Any]] = []
    athletes: dict[str, str] = {}
    work = fights_df.sort_values(["date", "fight_id"])
    if limit is not None:
        work = work.head(limit)

    with httpx.Client(timeout=60.0) as client:
        for i, row in enumerate(work.itertuples(index=False), start=1):
            fid = str(row.fight_id)
            if skip_existing and fid in existing_ids:
                continue
            parsed = fetch_fight_stats_row(
                client,
                event_id=str(row.event_id),
                fight_id=fid,
                date=str(row.date)[:10],
                season=int(row.season),
                home_team=str(row.home_team),
                away_team=str(row.away_team),
                athletes=athletes,
            )
            if parsed is not None:
                rows.append(parsed)
            if i % 25 == 0:
                logger.info("UFC fight stats: %d/%d fetched (%d rows)", i, len(work), len(rows))
            time.sleep(REQUEST_SLEEP_SECONDS)

    new_df = pd.DataFrame(rows)
    if new_df.empty and not PROCESSED_PARQUET.exists():
        logger.warning("No UFC fight stats ingested")
        return new_df

    if PROCESSED_PARQUET.exists() and not new_df.empty:
        prior = load_fight_stats()
        combined = pd.concat([prior, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["fight_id"], keep="last")
    elif not new_df.empty:
        combined = new_df
    else:
        return load_fight_stats() if PROCESSED_PARQUET.exists() else new_df

    PROCESSED_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(PROCESSED_PARQUET, index=False)
    logger.info("Wrote %s (%d fights)", PROCESSED_PARQUET, len(combined))
    return combined
