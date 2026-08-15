"""League settings — configurable slots (incl. WRT), maxes, bench, weights."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

POSITION_KEYS = ("QB", "RB", "WR", "TE", "DST", "K")

# Slot keys the UI / API can configure (counts expanded into a template).
SLOT_COUNT_KEYS = (
    "QB",
    "RB",
    "WR",
    "TE",
    "WRT",  # WR/RB/TE flex (alias: FLEX)
    "SUPERFLEX",
    "K",
    "DST",
    "BENCH",
    "IR",
)

# Default matches common ESPN + user's 2×WRT league shape.
DEFAULT_SLOT_COUNTS: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "WRT": 2,
    "SUPERFLEX": 0,
    "K": 1,
    "DST": 1,
    "BENCH": 6,
    "IR": 0,
}

DEFAULT_POSITION_MAXES: dict[str, int] = {
    "QB": 4,
    "RB": 8,
    "WR": 8,
    "TE": 3,
    "DST": 3,
    "K": 3,
}

WRT_ELIGIBLE = frozenset({"RB", "WR", "TE"})
SUPERFLEX_ELIGIBLE = frozenset({"QB", "RB", "WR", "TE"})
FLEX_ELIGIBLE = WRT_ELIGIBLE  # back-compat alias

DEFAULT_DRAFT_WEIGHTS: dict[str, float] = {
    # Team construction > raw season projection
    "projection": 0.06,
    "vorp": 0.20,
    "scarcity": 0.12,
    "tier_drop": 0.09,
    "roster_need": 0.18,
    "lineup_impact": 0.16,
    "adp_value": 0.05,
    "availability_urgency": 0.12,
    "upside": 0.05,
    "risk": 0.04,
    "roster_imbalance": 0.12,
    "bye": 0.02,
    "lookahead": 0.08,
    "team_usefulness": 0.10,
}


def normalize_slot_name(slot: str) -> str:
    s = str(slot or "").strip().upper()
    if s in ("FLEX", "W/R/T", "WR/RB/TE", "RB/WR/TE"):
        return "WRT"
    if s in ("SF", "SFLX", "SUPER FLEX"):
        return "SUPERFLEX"
    if s in ("DEF", "D/ST", "D"):
        return "DST"
    return s


def expand_slot_counts(counts: dict[str, int]) -> list[str]:
    """Expand QB:1 RB:2 … WRT:2 BENCH:6 into an ordered template."""
    order = (
        "QB",
        "RB",
        "WR",
        "TE",
        "WRT",
        "SUPERFLEX",
        "K",
        "DST",
        "BENCH",
        "IR",
    )
    out: list[str] = []
    for key in order:
        n = int(counts.get(key, 0) or 0)
        out.extend([key] * max(0, n))
    return out


def slot_counts_from_template(template: list[str] | tuple[str, ...]) -> dict[str, int]:
    counts = {k: 0 for k in SLOT_COUNT_KEYS}
    for raw in template:
        key = normalize_slot_name(raw)
        if key not in counts:
            if key == "BENCH":
                counts["BENCH"] += 1
            continue
        counts[key] += 1
    return counts


def normalize_slot_counts(raw: dict[str, Any] | None = None) -> dict[str, int]:
    out = dict(DEFAULT_SLOT_COUNTS)
    if not raw:
        return out
    for k, v in raw.items():
        key = normalize_slot_name(str(k))
        if key not in out and key != "FLEX":
            continue
        if key == "FLEX":
            key = "WRT"
        try:
            out[key] = max(0, min(20, int(v)))
        except (TypeError, ValueError):
            continue
    return out


@dataclass(frozen=True)
class LeagueSettings:
    league_size: int = 10
    scoring: str = "half_ppr"
    slot_counts: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SLOT_COUNTS)
    )
    position_maxes: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_POSITION_MAXES)
    )
    weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_DRAFT_WEIGHTS)
    )

    @property
    def superflex(self) -> bool:
        return int(self.slot_counts.get("SUPERFLEX", 0) or 0) > 0

    @property
    def flex_eligible(self) -> frozenset[str]:
        return WRT_ELIGIBLE

    @property
    def wrt_eligible(self) -> frozenset[str]:
        return WRT_ELIGIBLE

    @property
    def superflex_eligible(self) -> frozenset[str]:
        return SUPERFLEX_ELIGIBLE

    @property
    def full_template(self) -> list[str]:
        return expand_slot_counts(self.slot_counts)

    @property
    def starter_slots(self) -> list[str]:
        return [s for s in self.full_template if s not in ("BENCH", "IR")]

    @property
    def starter_template(self) -> tuple[str, ...]:
        return tuple(self.starter_slots)

    @property
    def bench_slots(self) -> int:
        return int(self.slot_counts.get("BENCH", 0) or 0)

    @property
    def ir_slots(self) -> int:
        return int(self.slot_counts.get("IR", 0) or 0)

    @property
    def wrt_slots(self) -> int:
        return int(self.slot_counts.get("WRT", 0) or 0)

    @property
    def roster_capacity(self) -> int:
        """Max non-IR players (starters + bench)."""
        return len(self.starter_slots) + self.bench_slots

    @property
    def roster_size(self) -> int:
        return self.roster_capacity

    @property
    def rounds(self) -> int:
        # IR usually filled from waivers; draft rounds = starters + bench
        return self.roster_capacity

    @property
    def total_picks(self) -> int:
        return self.league_size * self.rounds


# Back-compat names used elsewhere
DEFAULT_STARTER_TEMPLATE = [
    s for s in expand_slot_counts(DEFAULT_SLOT_COUNTS) if s not in ("BENCH", "IR")
]
DEFAULT_ROSTER_SIZE = sum(DEFAULT_SLOT_COUNTS.values()) - DEFAULT_SLOT_COUNTS.get("IR", 0)


def normalize_scoring(scoring: str | None) -> str:
    s = (scoring or "half_ppr").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("std", "standard", "non_ppr"):
        return "standard"
    if s in ("half", "half_ppr", "halfppr"):
        return "half_ppr"
    if s in ("ppr", "full_ppr", "fullppr"):
        return "ppr"
    return "half_ppr"


def normalize_position_maxes(maxes: dict[str, Any] | None = None) -> dict[str, int]:
    out = dict(DEFAULT_POSITION_MAXES)
    if not maxes:
        return out
    lower = {str(k).upper(): v for k, v in maxes.items()}
    for key in POSITION_KEYS:
        if key not in lower:
            continue
        try:
            val = int(lower[key])
        except (TypeError, ValueError):
            continue
        out[key] = max(0, min(20, val))
    return out


def league_settings_from_request(
    *,
    league_size: int,
    scoring: str | None = None,
    roster_size: int | None = None,
    roster_template: list[str] | None = None,
    slot_counts: dict[str, Any] | None = None,
    position_maxes: dict[str, Any] | None = None,
    superflex: bool = False,
    bench_slots: int | None = None,
    weights: dict[str, float] | None = None,
) -> LeagueSettings:
    if league_size < 2 or league_size > 20:
        raise ValueError("league_size must be 2–20")

    if slot_counts:
        counts = normalize_slot_counts(slot_counts)
    elif roster_template:
        counts = slot_counts_from_template(
            [normalize_slot_name(s) for s in roster_template]
        )
        # If template had no BENCH, derive from roster_size
        starters_n = sum(v for k, v in counts.items() if k not in ("BENCH", "IR"))
        if counts.get("BENCH", 0) == 0 and roster_size is not None:
            counts["BENCH"] = max(0, int(roster_size) - starters_n)
        elif counts.get("BENCH", 0) == 0 and bench_slots is not None:
            counts["BENCH"] = max(0, int(bench_slots))
    else:
        counts = dict(DEFAULT_SLOT_COUNTS)
        if roster_size is not None:
            starters_n = sum(
                v for k, v in counts.items() if k not in ("BENCH", "IR")
            )
            counts["BENCH"] = max(0, int(roster_size) - starters_n)
        if bench_slots is not None:
            counts["BENCH"] = max(0, int(bench_slots))

    if superflex and counts.get("SUPERFLEX", 0) == 0:
        counts["SUPERFLEX"] = 1

    w = dict(DEFAULT_DRAFT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})

    return LeagueSettings(
        league_size=int(league_size),
        scoring=normalize_scoring(scoring),
        slot_counts=counts,
        position_maxes=normalize_position_maxes(position_maxes),
        weights=w,
    )


def with_weights(settings: LeagueSettings, **deltas: float) -> LeagueSettings:
    w = dict(settings.weights)
    w.update(deltas)
    return replace(settings, weights=w)
