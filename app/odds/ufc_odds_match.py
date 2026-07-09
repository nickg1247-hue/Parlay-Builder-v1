"""Fuzzy fight ↔ odds matching (corner order, aliases)."""

from __future__ import annotations

import pandas as pd

from app.odds.ufc_fighter_aliases import fighter_match_key, fighters_match


def _keys_for_row(
    home_team: str, away_team: str
) -> tuple[str, str, frozenset[str]]:
    hk = fighter_match_key(home_team)
    ak = fighter_match_key(away_team)
    return hk, ak, frozenset({hk, ak})


def _odds_row_matches_fight(
    fight_home: str,
    fight_away: str,
    odds_home: str,
    odds_away: str,
) -> str | None:
    """Return 'direct', 'swapped', or None."""
    fh, fa, fset = _keys_for_row(fight_home, fight_away)
    oh, oa, oset = _keys_for_row(odds_home, odds_away)
    if not fh or not fa or not oh or not oa:
        return None
    if fset == oset:
        if fh == oh and fa == oa:
            return "direct"
        if fh == oa and fa == oh:
            return "swapped"
        if fighters_match(fight_home, odds_home) and fighters_match(fight_away, odds_away):
            return "direct"
        if fighters_match(fight_home, odds_away) and fighters_match(fight_away, odds_home):
            return "swapped"
    return None


def merge_fights_odds_fuzzy(fights: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """
    Match fights to odds on date + fighter keys, allowing swapped corners.

    Returns fight rows with home_ml, away_ml aligned to fight home/away.
    """
    if fights.empty or odds.empty:
        return pd.DataFrame()

    g = fights.copy()
    o = odds.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")

    odds_by_date: dict[str, list[dict[str, object]]] = {}
    for rec in o.to_dict(orient="records"):
        odds_by_date.setdefault(str(rec["date"]), []).append(rec)

    matched_rows: list[dict[str, object]] = []
    used_odds: set[tuple[str, str, str, str]] = set()

    for rec in g.to_dict(orient="records"):
        day = str(rec["date"])
        home = str(rec.get("home_team") or "")
        away = str(rec.get("away_team") or "")
        if not home or not away:
            continue
        for odds_rec in odds_by_date.get(day, []):
            o_home = str(odds_rec.get("home_team") or "")
            o_away = str(odds_rec.get("away_team") or "")
            dedupe_key = (day, o_home, o_away, str(odds_rec.get("home_ml")))
            if dedupe_key in used_odds:
                continue
            orient = _odds_row_matches_fight(home, away, o_home, o_away)
            if orient is None:
                continue
            row = dict(rec)
            if orient == "direct":
                row["home_ml"] = odds_rec.get("home_ml")
                row["away_ml"] = odds_rec.get("away_ml")
            else:
                row["home_ml"] = odds_rec.get("away_ml")
                row["away_ml"] = odds_rec.get("home_ml")
            if odds_rec.get("odds_source"):
                row["odds_source"] = odds_rec["odds_source"]
            matched_rows.append(row)
            used_odds.add(dedupe_key)
            break

    if not matched_rows:
        return pd.DataFrame()
    return pd.DataFrame(matched_rows)


def match_diagnostics(
    fights: pd.DataFrame, odds: pd.DataFrame, sample: int = 20
) -> dict[str, object]:
    """Summarize unmatched holdout fights for DEV / debugging."""
    g = fights.copy()
    o = odds.copy()
    g["date"] = pd.to_datetime(g["date"]).dt.strftime("%Y-%m-%d")
    o["date"] = pd.to_datetime(o["date"]).dt.strftime("%Y-%m-%d")
    matched = merge_fights_odds_fuzzy(g, o)
    holdout_n = len(g)
    matched_n = len(matched)
    failures: list[dict[str, str]] = []
    for rec in g.to_dict(orient="records"):
        day = str(rec["date"])
        home = str(rec.get("home_team") or "")
        away = str(rec.get("away_team") or "")
        day_odds = o[o["date"] == day]
        if day_odds.empty:
            reason = "no_odds_for_date"
        else:
            found = False
            for orow in day_odds.to_dict(orient="records"):
                if _odds_row_matches_fight(
                    home, away, str(orow["home_team"]), str(orow["away_team"])
                ):
                    found = True
                    break
            reason = "fighter_name_mismatch" if not found else "matched"
        if reason != "matched" and len(failures) < sample:
            failures.append(
                {
                    "date": day,
                    "home_team": home,
                    "away_team": away,
                    "reason": reason,
                }
            )
    return {
        "holdout_fights": holdout_n,
        "matched_fights": matched_n,
        "match_rate_pct": round(100.0 * matched_n / holdout_n, 2) if holdout_n else 0.0,
        "sample_failures": failures,
    }
