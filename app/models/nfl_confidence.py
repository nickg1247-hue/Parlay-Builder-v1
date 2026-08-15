"""Empirical NFL pick categories: toss-up, soft, hard, lock.

Cuts are fit from walk-forward favorite % vs actual winners — not guessed.
A band is only labeled soft/hard/lock if that exact group of games hits
60% / 75% / 95%. Toss-up is everything weaker than the soft floor.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterable

from app.config import PROJECT_ROOT

CATEGORIES = ("toss-up", "soft", "hard", "lock")
CATEGORY_LABELS = {
    "toss-up": "Toss-up",
    "soft": "Soft",
    "hard": "Hard",
    "lock": "Lock",
}
FLOORS = {"soft": 0.60, "hard": 0.75, "lock": 0.95}
MIN_GAMES = {"soft": 25, "hard": 15, "lock": 8}
STEP = 0.5
BIN5 = 5.0
LOCK_MIN_LO = 75.0
PRESEASON_MAX = "soft"
CUTS_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_confidence_cuts.json"
DEFAULT_CUTS = {
    "cuts": {
        "toss-up": {"lo": 50.0, "hi": 55.0},
        "soft": {"lo": 55.0, "hi": 73.0},
        "hard": {"lo": 73.0, "hi": 80.5},
        "lock": {"lo": 80.5, "hi": 86.0},
        "hard_tail": {"lo": 86.0, "hi": 100.01},
    }
}


def category_label(name: str) -> str:
    return CATEGORY_LABELS.get(name, "Toss-up")


@lru_cache(maxsize=1)
def load_category_cuts() -> dict[str, Any]:
    if CUTS_JSON.exists():
        try:
            payload = json.loads(CUTS_JSON.read_text(encoding="utf-8"))
            if payload.get("cuts"):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CUTS)


def category_for_proba(home_prob: float, game_type: str | None = None) -> str:
    home_pct = float(home_prob) * 100.0
    return assign_category(home_pct, 100.0 - home_pct, load_category_cuts(), game_type=game_type)


def favorite_pct(home_pct: float, away_pct: float | None = None) -> float:
    home = float(home_pct)
    away = float(away_pct) if away_pct is not None else 100.0 - home
    return max(home, away)


def _band_stats(games: list[dict[str, Any]], lo: float, hi: float) -> tuple[int, int]:
    n = 0
    correct = 0
    for g in games:
        fav = favorite_pct(g["home_pct"], g.get("away_pct"))
        if lo <= fav < hi:
            n += 1
            correct += int(g.get("correct") or 0)
    return n, correct


def _hit(games: list[dict[str, Any]], lo: float, hi: float) -> tuple[int, int, float | None]:
    n, c = _band_stats(games, lo, hi)
    return n, c, (c / n if n else None)


def _smallest_lo(games: list[dict[str, Any]], hi: float, floor: float, min_n: int) -> float | None:
    """Lowest favorite-% start so [lo, hi) still hits `floor` with enough games."""
    best: float | None = None
    t = 50.0
    while t < hi - 1e-9:
        n, _c, rate = _hit(games, t, hi)
        if n >= min_n and rate is not None and rate + 1e-12 >= floor:
            best = t
            break
        t += STEP
    return best


def _toss_hi_from_bins(games: list[dict[str, Any]]) -> float:
    """First 5-point bin from 50% that hits the soft floor. Below that is toss-up."""
    t = 50.0
    while t < 100:
        n, _c, rate = _hit(games, t, t + BIN5)
        if n == 0:
            t += BIN5
            continue
        if rate is not None and rate + 1e-12 < FLOORS["soft"]:
            t += BIN5
            continue
        break
    return t


def _lock_band(games: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """[lo, 100) if that hits 95%, else the highest interior band that does."""
    lock_lo = _smallest_lo(games, 100.01, FLOORS["lock"], MIN_GAMES["lock"])
    if lock_lo is not None:
        return lock_lo, 100.01
    best: tuple[float, float] | None = None
    lo = LOCK_MIN_LO
    while lo < 99:
        hi = lo + 3.0
        while hi <= 100.01 + 1e-9:
            n, _c, rate = _hit(games, lo, hi)
            if (
                n >= MIN_GAMES["lock"]
                and rate is not None
                and rate + 1e-12 >= FLOORS["lock"]
            ):
                if best is None or lo > best[0] or (lo == best[0] and hi > best[1]):
                    best = (lo, hi)
            hi += STEP
        lo += STEP
    return (best[0], best[1]) if best else (None, None)


def fit_category_cuts(games: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [g for g in games if g.get("home_pct") is not None]
    lock_lo, lock_hi = _lock_band(rows)
    hard_hi = lock_lo if lock_lo is not None else 100.01
    hard_lo = _smallest_lo(rows, 100.01, FLOORS["hard"], MIN_GAMES["hard"])
    if hard_lo is not None and lock_lo is not None and hard_lo >= lock_lo:
        hard_lo = _smallest_lo(rows, lock_lo, FLOORS["hard"], MIN_GAMES["hard"])
    soft_hi = hard_lo if hard_lo is not None else (lock_lo if lock_lo is not None else 100.01)
    toss_hi = _toss_hi_from_bins(rows)
    soft_lo = _smallest_lo(rows, soft_hi, FLOORS["soft"], MIN_GAMES["soft"])
    if soft_lo is not None and soft_lo < toss_hi:
        n, _c, rate = _hit(rows, toss_hi, soft_hi)
        if n >= MIN_GAMES["soft"] and rate is not None and rate + 1e-12 >= FLOORS["soft"]:
            soft_lo = toss_hi
        else:
            soft_lo = _smallest_lo(rows, soft_hi, FLOORS["soft"], MIN_GAMES["soft"])
            if soft_lo is not None and soft_lo < toss_hi:
                toss_hi = soft_lo
    if soft_lo is None:
        toss_hi = min(toss_hi, soft_hi)
    else:
        toss_hi = min(toss_hi, soft_lo)

    hard_tail = None
    if lock_lo is not None and lock_hi is not None and lock_hi < 100:
        hard_tail = {"lo": lock_hi, "hi": 100.01}

    cuts = {
        "toss-up": {"lo": 50.0, "hi": toss_hi},
        "soft": {"lo": soft_lo, "hi": soft_hi} if soft_lo is not None else None,
        "hard": {"lo": hard_lo, "hi": hard_hi} if hard_lo is not None else None,
        "lock": {"lo": lock_lo, "hi": lock_hi} if lock_lo is not None else None,
        "hard_tail": hard_tail,
    }
    return {
        "method": (
            "Favorite % = max(home, away) walk-forward win probability. "
            "Toss-up is each 5-point bin from 50% that still hits under 60%. "
            "Soft / hard / lock bands must hit 60% / 75% / 95%. "
            "Lock may be an interior band when a higher favorite-% upset would "
            "break a [cut, 100) tail. Preseason is capped at soft."
        ),
        "floors": {k: int(v * 100) for k, v in FLOORS.items()},
        "min_games": dict(MIN_GAMES),
        "cuts": cuts,
    }


def _in_band(fav: float, band: dict[str, Any] | None) -> bool:
    if not band or band.get("lo") is None:
        return False
    lo = float(band["lo"])
    hi = float(band.get("hi") or 100.01)
    return lo <= fav < hi


def assign_category(
    home_pct: float,
    away_pct: float | None,
    cuts: dict[str, Any],
    game_type: str | None = None,
) -> str:
    fav = favorite_pct(home_pct, away_pct)
    table = cuts.get("cuts") or cuts
    name = "toss-up"
    if _in_band(fav, table.get("lock")):
        name = "lock"
    elif _in_band(fav, table.get("hard")) or _in_band(fav, table.get("hard_tail")):
        name = "hard"
    elif _in_band(fav, table.get("soft")):
        name = "soft"
    elif _in_band(fav, table.get("toss-up")):
        name = "toss-up"
    if game_type == "preseason" and name in ("hard", "lock"):
        return PRESEASON_MAX
    return name


def strongest_tail(games: Iterable[dict[str, Any]], min_n: int = 8) -> dict[str, Any] | None:
    """Best [favorite %, 100) hit rate with at least `min_n` games."""
    rows = [g for g in games if g.get("home_pct") is not None]
    best: dict[str, Any] | None = None
    t = 50.0
    while t < 99:
        n, c, rate = _hit(rows, t, 100.01)
        if n >= min_n and rate is not None:
            cand = {
                "lo": t,
                "games": n,
                "correct": c,
                "hit_pct": round(rate * 100, 1),
            }
            if best is None or cand["hit_pct"] > best["hit_pct"]:
                best = cand
        t += STEP
    return best


def cumulative_tails(
    games: Iterable[dict[str, Any]],
    cuts: Iterable[float] = (50, 60, 66, 70, 74, 76, 80, 82, 86, 90),
) -> list[dict[str, Any]]:
    rows = list(games)
    out = []
    for lo in cuts:
        n, c, rate = _hit(rows, float(lo), 100.01)
        if n:
            out.append(
                {
                    "favorite_range": f">={lo:g}%",
                    "lo": float(lo),
                    "games": n,
                    "correct": c,
                    "wrong": n - c,
                    "hit_pct": round(rate * 100, 1) if rate is not None else None,
                }
            )
    return out


def category_proof(games: Iterable[dict[str, Any]], cuts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(games)
    table = cuts.get("cuts") or cuts
    proof = []
    for name in CATEGORIES:
        band = table.get(name)
        floor = FLOORS.get(name)
        if not band or band.get("lo") is None:
            tail = strongest_tail(rows, MIN_GAMES.get(name, 8))
            if tail:
                note = (
                    f"No favorite-% band with at least {MIN_GAMES.get(name, 8)} games "
                    f"hit the {int(floor * 100) if floor else '?'}% floor. "
                    f"Best tail: favorites >={tail['lo']:g}% went "
                    f"{tail['correct']}/{tail['games']} ({tail['hit_pct']}%)."
                )
            else:
                note = "No games reached this floor with enough sample."
            proof.append(
                {
                    "category": name,
                    "favorite_range": None,
                    "games": 0,
                    "correct": 0,
                    "wrong": 0,
                    "hit_pct": None,
                    "floor_pct": int(floor * 100) if floor else None,
                    "meets_floor": name == "toss-up",
                    "note": note,
                    "best_tail": tail,
                }
            )
            continue
        lo, hi = float(band["lo"]), float(band["hi"])
        n, c, rate = _hit(rows, lo, hi)
        hi_label = "100" if hi >= 100 else f"{hi:g}"
        item = {
            "category": name,
            "favorite_range": f"{lo:g}–{hi_label}%",
            "lo": lo,
            "hi": hi,
            "games": n,
            "correct": c,
            "wrong": n - c,
            "hit_pct": round(rate * 100, 1) if rate is not None else None,
            "floor_pct": int(floor * 100) if floor else None,
            "meets_floor": True if name == "toss-up" else bool(rate is not None and floor and rate >= floor),
        }
        if name == "hard" and table.get("hard_tail"):
            tail = table["hard_tail"]
            tn, tc, _tr = _hit(rows, float(tail["lo"]), float(tail["hi"]))
            n += tn
            c += tc
            rate = c / n if n else None
            item["games"] = n
            item["correct"] = c
            item["wrong"] = n - c
            item["hit_pct"] = round(rate * 100, 1) if rate is not None else None
            item["meets_floor"] = bool(rate is not None and floor and rate >= floor)
            item["favorite_range"] = (
                f"{lo:g}–{hi_label}% + {float(tail['lo']):g}–100%"
            )
        if name == "toss-up":
            item["note"] = (
                f"Favorites from 50% up to {hi_label}% went "
                f"{c}/{n} ({item['hit_pct']}%) — treated as a coin flip."
            )
        else:
            item["note"] = (
                f"Favorites {item['favorite_range']} went {c}/{n} "
                f"({item['hit_pct']}%). Floor is {item['floor_pct']}%."
            )
        proof.append(item)
    return proof


def apply_categories(games: list[dict[str, Any]], cuts: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for g in games:
        row = dict(g)
        row["favorite_pct"] = round(favorite_pct(g["home_pct"], g.get("away_pct")), 1)
        row["category"] = assign_category(
            g["home_pct"],
            g.get("away_pct"),
            cuts,
            game_type=g.get("game_type"),
        )
        out.append(row)
    return out


def labeled_proof(games: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hit rate of the labels actually stamped on games."""
    buckets = {name: {"games": 0, "correct": 0} for name in CATEGORIES}
    for g in games:
        cat = g.get("category") or "toss-up"
        if cat not in buckets:
            continue
        buckets[cat]["games"] += 1
        buckets[cat]["correct"] += int(g.get("correct") or 0)
    proof = []
    for name in CATEGORIES:
        n = buckets[name]["games"]
        c = buckets[name]["correct"]
        floor = FLOORS.get(name)
        rate = c / n if n else None
        proof.append(
            {
                "category": name,
                "games": n,
                "correct": c,
                "wrong": n - c,
                "hit_pct": round(rate * 100, 1) if rate is not None else None,
                "floor_pct": int(floor * 100) if floor else None,
                "meets_floor": True
                if name == "toss-up"
                else bool(rate is not None and floor and rate >= floor),
            }
        )
    return proof


def bin_hit_rates(games: Iterable[dict[str, Any]], width: float = 2.0) -> list[dict[str, Any]]:
    """Proof table: actual hit rate by favorite-% bin."""
    rows = list(games)
    bins: list[dict[str, Any]] = []
    start = 50.0
    while start < 100:
        end = min(100.01, start + width)
        n, c, rate = _hit(rows, start, end)
        if n:
            bins.append(
                {
                    "favorite_range": f"{start:g}–{end if end < 100 else 100:g}%",
                    "games": n,
                    "correct": c,
                    "hit_pct": round(rate * 100, 1) if rate is not None else None,
                }
            )
        start += width
    return bins
