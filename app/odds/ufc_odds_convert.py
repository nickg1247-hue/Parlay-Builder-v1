"""Convert external UFC odds CSV formats to canonical moneyline rows."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.odds.team_aliases import is_valid_american_odds
from app.odds.ufc_fighter_aliases import normalize_fighter_name

CANONICAL_COLUMNS = ("date", "home_team", "away_team", "home_ml", "away_ml")

# Column alias groups → canonical field
_DATE_ALIASES = ("date", "event_date", "fight_date", "game_date")
_HOME_ALIASES = ("home_team", "home_fighter", "fighter_home", "f1", "fighter1")
_AWAY_ALIASES = ("away_team", "away_fighter", "fighter_away", "f2", "fighter2")
_HOME_ML_ALIASES = ("home_ml", "home_odds", "ml_home", "f1_ml", "fighter1_ml")
_AWAY_ML_ALIASES = ("away_ml", "away_odds", "ml_away", "f2_ml", "fighter2_ml")


def _first_col(df: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        if alias in lower:
            return lower[alias]
    return None


def _parse_date(val: object) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _parse_american(val: object) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    try:
        num = int(float(text))
    except ValueError:
        return None
    return num if is_valid_american_odds(num) else None


def _decimal_to_american(decimal: float) -> int | None:
    if decimal <= 1.0:
        return None
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100))
    return int(round(-100 / (decimal - 1.0)))


def _parse_odds_value(val: object) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip()
    if not text:
        return None
    if text.startswith("+") or text.startswith("-") or re.match(r"^-?\d+$", text):
        return _parse_american(text)
    try:
        dec = float(text)
    except ValueError:
        return None
    if 1.0 < dec < 50:
        return _decimal_to_american(dec)
    return _parse_american(dec)


def _normalize_name_cell(val: object) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    raw = str(val).strip()
    if "," in raw and not raw.startswith('"'):
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2:
            raw = f"{parts[1]} {parts[0]}"
    raw = re.sub(r'^["\']|["\']$', "", raw)
    raw = re.sub(r"\s+\(.+\)$", "", raw)
    return normalize_fighter_name(raw)


def detect_format(path: Path) -> str:
    """Return format id: canonical, mikespa, betmma, favourite."""
    df = pd.read_csv(path, nrows=5)
    cols = {str(c).strip().lower() for c in df.columns}
    if {"r_fighter", "b_fighter", "r_odds", "b_odds"}.issubset(cols):
        return "mikespa"
    if "favourite" in cols and "underdog" in cols:
        return "favourite"
    if "fighter1" in cols and "fighter2" in cols and (
        "favourite_odds" in cols or "f1_ml" in cols
    ):
        return "betmma"
    if _first_col(df, _DATE_ALIASES) and _first_col(df, _HOME_ML_ALIASES):
        return "canonical"
    return "unknown"


def convert_mikespa(df: pd.DataFrame) -> pd.DataFrame:
    """MikeSpa ufc-master: R_fighter/B_fighter with R_odds/B_odds (red=home convention)."""
    out: list[dict[str, object]] = []
    for row in df.itertuples(index=False):
        d = _parse_date(getattr(row, "date", None))
        home = _normalize_name_cell(getattr(row, "R_fighter", None))
        away = _normalize_name_cell(getattr(row, "B_fighter", None))
        home_ml = _parse_odds_value(getattr(row, "R_odds", None))
        away_ml = _parse_odds_value(getattr(row, "B_odds", None))
        if not d or not home or not away or home_ml is None or away_ml is None:
            continue
        out.append(
            {
                "date": d,
                "home_team": home,
                "away_team": away,
                "home_ml": home_ml,
                "away_ml": away_ml,
                "odds_source": "mikespa_master",
            }
        )
    return pd.DataFrame(out)


def convert_favourite_underdog(df: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, object]] = []
    date_col = _first_col(df, _DATE_ALIASES)
    fav_odds_col = next(
        (c for c in df.columns if str(c).lower() in ("favourite_odds", "favorite_odds")),
        None,
    )
    dog_odds_col = next(
        (c for c in df.columns if str(c).lower() in ("underdog_odds",)), None
    )
    if not date_col or not fav_odds_col or not dog_odds_col:
        return pd.DataFrame()
    for rec in df.to_dict(orient="records"):
        d = _parse_date(rec.get(date_col))
        fav = _normalize_name_cell(rec.get("favourite") or rec.get("favorite"))
        dog = _normalize_name_cell(rec.get("underdog"))
        fav_ml = _parse_odds_value(rec.get(fav_odds_col))
        dog_ml = _parse_odds_value(rec.get(dog_odds_col))
        if not d or not fav or not dog or fav_ml is None or dog_ml is None:
            continue
        out.append(
            {
                "date": d,
                "home_team": fav,
                "away_team": dog,
                "home_ml": fav_ml,
                "away_ml": dog_ml,
                "odds_source": "favourite_underdog",
            }
        )
    return pd.DataFrame(out)


def convert_canonical_flexible(df: pd.DataFrame) -> pd.DataFrame:
    date_col = _first_col(df, _DATE_ALIASES)
    home_col = _first_col(df, _HOME_ALIASES)
    away_col = _first_col(df, _AWAY_ALIASES)
    home_ml_col = _first_col(df, _HOME_ML_ALIASES)
    away_ml_col = _first_col(df, _AWAY_ML_ALIASES)
    if not all([date_col, home_col, away_col, home_ml_col, away_ml_col]):
        return pd.DataFrame()
    out: list[dict[str, object]] = []
    for rec in df.to_dict(orient="records"):
        d = _parse_date(rec.get(date_col))
        home = _normalize_name_cell(rec.get(home_col))
        away = _normalize_name_cell(rec.get(away_col))
        home_ml = _parse_odds_value(rec.get(home_ml_col))
        away_ml = _parse_odds_value(rec.get(away_ml_col))
        if not d or not home or not away or home_ml is None or away_ml is None:
            continue
        out.append(
            {
                "date": d,
                "home_team": home,
                "away_team": away,
                "home_ml": home_ml,
                "away_ml": away_ml,
                "odds_source": "canonical_csv",
            }
        )
    return pd.DataFrame(out)


def convert_odds_file(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    fmt = detect_format(path)
    if fmt == "mikespa":
        converted = convert_mikespa(df)
    elif fmt == "favourite":
        converted = convert_favourite_underdog(df)
    elif fmt == "canonical":
        converted = convert_canonical_flexible(df)
    elif fmt == "betmma":
        converted = convert_canonical_flexible(df)
    else:
        converted = convert_canonical_flexible(df)
        if converted.empty:
            raise ValueError(f"Unrecognized UFC odds format: {path}")
    if converted.empty:
        return converted
    return converted.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)


def merge_odds_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    merged = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    if merged.empty:
        return merged
    if "priority" not in merged.columns:
        merged["priority"] = 0
    merged = merged.sort_values(["priority", "date"]).drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    )
    return merged.drop(columns=["priority"], errors="ignore").reset_index(drop=True)
