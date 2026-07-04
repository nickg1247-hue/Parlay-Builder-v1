"""UFC fight ingest via ESPN Core + Site MMA APIs (UFC events 2021–2025)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.db.ufc_schema import UFC_FIGHTS_COLUMNS, ensure_ufc_fights_table
from app.odds.ufc_fighter_aliases import normalize_fighter_name

logger = logging.getLogger(__name__)

ESPN_UFC_EVENTS = "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events"
SEASONS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
PROCESSED_PARQUET = PROJECT_ROOT / "data" / "processed" / "ufc_fights.parquet"
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "ufc_fights.csv"
MAX_REST_GAP_DAYS = 365
DEFAULT_REST_FILL = 90.0
REQUEST_SLEEP_SECONDS = 0.15
REQUEST_RETRIES = 4
EVENT_PAGE_LIMIT = 100


@dataclass
class ParsedFight:
    fight_id: str
    event_id: str
    event_name: str
    date: str
    season: int
    home_team: str
    away_team: str
    home_win: int
    weight_class: str = ""
    card_segment: str = ""


class _AthleteCache:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._names: dict[str, str] = {}

    def resolve(self, athlete: dict[str, Any]) -> str:
        if not athlete:
            return ""
        if athlete.get("displayName"):
            return normalize_fighter_name(str(athlete["displayName"]))
        ref = athlete.get("$ref")
        if not ref:
            return ""
        if ref in self._names:
            return self._names[ref]
        for attempt in range(REQUEST_RETRIES):
            try:
                data = self._client.get(ref, timeout=30.0).json()
                name = normalize_fighter_name(
                    str(data.get("displayName") or data.get("fullName") or "")
                )
                self._names[ref] = name
                time.sleep(REQUEST_SLEEP_SECONDS)
                return name
            except (httpx.HTTPError, ValueError) as exc:
                if attempt + 1 >= REQUEST_RETRIES:
                    logger.warning("Athlete fetch failed %s: %s", ref, exc)
                    return ""
                time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
        return ""


def _parse_event_date(raw: str) -> str:
    if not raw:
        return ""
    return raw[:10]


def _card_segment(comp: dict[str, Any]) -> str:
    seg = comp.get("cardSegment")
    if isinstance(seg, dict):
        return str(seg.get("text") or seg.get("name") or "")
    return str(seg or "")


def _competition_winner(comp: dict[str, Any], athletes: _AthleteCache) -> ParsedFight | None:
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    by_order: dict[int, dict[str, Any]] = {}
    for c in competitors:
        order = c.get("order")
        if order is not None:
            by_order[int(order)] = c
    home_c = by_order.get(1) or competitors[0]
    away_c = by_order.get(2) or competitors[1]
    home_name = athletes.resolve(home_c.get("athlete") or {})
    away_name = athletes.resolve(away_c.get("athlete") or {})
    if not home_name or not away_name:
        return None
    home_won = bool(home_c.get("winner"))
    away_won = bool(away_c.get("winner"))
    if home_won == away_won:
        return None
    home_win = 1 if home_won else 0
    weight = (comp.get("type") or {}).get("text") or ""
    fight_id = str(comp.get("id") or "")
    if not fight_id:
        return None
    return ParsedFight(
        fight_id=fight_id,
        event_id="",
        event_name="",
        date="",
        season=0,
        home_team=home_name,
        away_team=away_name,
        home_win=home_win,
        weight_class=str(weight),
        card_segment=_card_segment(comp),
    )


def _fetch_event_refs(client: httpx.Client, season: int) -> list[str]:
    refs: list[str] = []
    page = 1
    dates = f"{season}0101-{season}1231"
    while True:
        params = {"dates": dates, "limit": EVENT_PAGE_LIMIT, "page": page}
        last_error: Exception | None = None
        data: dict[str, Any] | None = None
        for attempt in range(REQUEST_RETRIES):
            try:
                response = client.get(ESPN_UFC_EVENTS, params=params, timeout=60.0)
                response.raise_for_status()
                data = response.json()
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
        if data is None:
            raise RuntimeError(f"ESPN UFC events {season} page {page} failed: {last_error}")
        items = data.get("items") or []
        for item in items:
            ref = item.get("$ref")
            if ref:
                refs.append(ref)
        count = int(data.get("count") or 0)
        if page * EVENT_PAGE_LIMIT >= count or not items:
            break
        page += 1
        time.sleep(REQUEST_SLEEP_SECONDS)
    return refs


def _fetch_event_fights(
    client: httpx.Client,
    event_ref: str,
    athletes: _AthleteCache,
) -> list[ParsedFight]:
    for attempt in range(REQUEST_RETRIES):
        try:
            event = client.get(event_ref, timeout=60.0).json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            if attempt + 1 >= REQUEST_RETRIES:
                logger.warning("Event fetch failed %s: %s", event_ref, exc)
                return []
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 1))
    else:
        return []

    event_id = str(event.get("id") or "")
    event_name = str(event.get("name") or event.get("shortName") or "UFC Event")
    event_date = _parse_event_date(str(event.get("date") or ""))
    if not event_date:
        return []
    season = int(event_date[:4])

    fights: list[ParsedFight] = []
    for comp in event.get("competitions") or []:
        if isinstance(comp, dict) and "$ref" in comp and "competitors" not in comp:
            try:
                comp = client.get(comp["$ref"], timeout=30.0).json()
                time.sleep(REQUEST_SLEEP_SECONDS)
            except (httpx.HTTPError, ValueError):
                continue
        parsed = _competition_winner(comp, athletes)
        if parsed is None:
            continue
        parsed.event_id = event_id
        parsed.event_name = event_name
        parsed.date = event_date
        parsed.season = season
        fights.append(parsed)
    return fights


def fetch_raw_fights() -> list[ParsedFight]:
    """Pull completed UFC fights from ESPN (one event request per card)."""
    all_fights: list[ParsedFight] = []
    with httpx.Client(timeout=60.0) as client:
        athletes = _AthleteCache(client)
        for season in SEASONS:
            refs = _fetch_event_refs(client, season)
            logger.info("UFC ingest %s: %d events", season, len(refs))
            for i, ref in enumerate(refs, start=1):
                fights = _fetch_event_fights(client, ref, athletes)
                all_fights.extend(fights)
                if i % 10 == 0:
                    logger.info("  %s: %d/%d events (%d fights)", season, i, len(refs), len(all_fights))
                time.sleep(REQUEST_SLEEP_SECONDS)

    seen: set[str] = set()
    unique: list[ParsedFight] = []
    for fight in all_fights:
        if fight.fight_id in seen:
            continue
        seen.add(fight.fight_id)
        unique.append(fight)
    unique.sort(key=lambda f: (f.date, f.fight_id))
    logger.info("Total completed UFC fights: %s", len(unique))
    return unique


def _fights_to_frame(fights: list[ParsedFight]) -> pd.DataFrame:
    records = [
        {
            "fight_id": f.fight_id,
            "event_id": f.event_id,
            "event_name": f.event_name,
            "date": f.date,
            "season": f.season,
            "home_team": f.home_team,
            "away_team": f.away_team,
            "home_win": int(f.home_win),
            "weight_class": f.weight_class,
            "card_segment": f.card_segment,
        }
        for f in fights
    ]
    return pd.DataFrame(records)


def _collect_rest_gaps(df: pd.DataFrame) -> list[int]:
    gaps: list[int] = []
    team_last: dict[str, datetime] = {}
    for row in df.sort_values(["date", "fight_id"]).itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")
        for team in (row.home_team, row.away_team):
            prev = team_last.get(team)
            if prev is not None:
                gap = (game_date - prev).days
                if 0 < gap <= MAX_REST_GAP_DAYS:
                    gaps.append(gap)
            team_last[team] = game_date
    return gaps


def _attach_rest_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["date", "fight_id"]).reset_index(drop=True)
    rest_fill = float(np.median(_collect_rest_gaps(out))) if len(out) else DEFAULT_REST_FILL
    if np.isnan(rest_fill):
        rest_fill = DEFAULT_REST_FILL

    team_last: dict[str, datetime] = {}
    home_rest: list[float] = []
    away_rest: list[float] = []
    home_b2b: list[int] = []
    away_b2b: list[int] = []

    for row in out.itertuples(index=False):
        game_date = datetime.strptime(row.date, "%Y-%m-%d")
        h_prev = team_last.get(row.home_team)
        a_prev = team_last.get(row.away_team)
        h_gap = (game_date - h_prev).days if h_prev else rest_fill
        a_gap = (game_date - a_prev).days if a_prev else rest_fill
        if h_gap > MAX_REST_GAP_DAYS or h_gap < 0:
            h_gap = rest_fill
        if a_gap > MAX_REST_GAP_DAYS or a_gap < 0:
            a_gap = rest_fill
        home_rest.append(float(h_gap))
        away_rest.append(float(a_gap))
        home_b2b.append(1 if h_prev and (game_date - h_prev).days <= 7 else 0)
        away_b2b.append(1 if a_prev and (game_date - a_prev).days <= 7 else 0)
        team_last[row.home_team] = game_date
        team_last[row.away_team] = game_date

    out = out.copy()
    out["home_rest_days"] = home_rest
    out["away_rest_days"] = away_rest
    out["home_b2b"] = home_b2b
    out["away_b2b"] = away_b2b
    return out


def _write_sqlite(df: pd.DataFrame) -> None:
    conn = get_connection()
    try:
        ensure_ufc_fights_table(conn)
        conn.execute("DELETE FROM ufc_fights")
        df[UFC_FIGHTS_COLUMNS].to_sql("ufc_fights", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()


def run_ingest() -> pd.DataFrame:
    fights = fetch_raw_fights()
    if not fights:
        raise SystemExit("No UFC fights ingested — ESPN API may be unavailable.")
    df = _fights_to_frame(fights)
    df = _attach_rest_features(df)
    PROCESSED_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PROCESSED_PARQUET, index=False)
    df.to_csv(PROCESSED_CSV, index=False)
    _write_sqlite(df)
    logger.info("Wrote %s (%d fights)", PROCESSED_PARQUET, len(df))
    return df
