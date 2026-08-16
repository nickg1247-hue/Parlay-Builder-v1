"""CFP 12-team field selection — committee-style résumé, year-specific rules.

2024: five highest-ranked conference champions + seven at-large.
      Byes = four highest-ranked conference champions.
2025: same auto-bid rule; byes = four highest-ranked teams overall.
2026+: Power 4 champions always in, plus the top Group of 6 team,
       plus seven at-large. Byes = four highest-ranked teams overall.

Committee score favors P4 / Notre Dame quality over G5 win totals.
G5 teams almost never take an at-large (2024–2025: zero G5 at-larges).
"""

from __future__ import annotations

from typing import Any

POWER_KEYS = frozenset({"sec", "big_ten", "big_12", "acc"})
GROUP_KEYS = frozenset({"aac", "mwc", "sun_belt", "mac", "cusa", "pac_12"})
INDEPENDENT_KEY = "independent"

# Official 12-team fields (seed order). Used to tune and backtest.
ACTUAL_FIELDS: dict[int, tuple[str, ...]] = {
    2024: (
        "Oregon",
        "Georgia",
        "Boise State",
        "Arizona State",
        "Texas",
        "Penn State",
        "Notre Dame",
        "Ohio State",
        "Tennessee",
        "Indiana",
        "SMU",
        "Clemson",
    ),
    2025: (
        "Indiana",
        "Ohio State",
        "Georgia",
        "Texas Tech",
        "Oregon",
        "Ole Miss",
        "Texas A&M",
        "Oklahoma",
        "Alabama",
        "Miami",
        "Tulane",
        "James Madison",
    ),
}

ACTUAL_FIRST_OUT: dict[int, tuple[str, ...]] = {
    2024: ("Alabama", "Miami"),
    2025: ("Notre Dame", "BYU", "Texas", "Vanderbilt"),
}

AUTO_BIDS = 5
PLAYOFF_FIELD = 12
BYE_SEEDS = 4


def is_power(conf_key: str) -> bool:
    return conf_key in POWER_KEYS or conf_key == INDEPENDENT_KEY


def is_group(conf_key: str) -> bool:
    return conf_key in GROUP_KEYS


def playoff_rules_year(season: int) -> str:
    """Which auto-bid / bye rules apply for this season."""
    if season <= 2024:
        return "champs5_bye_champs"
    if season == 2025:
        return "champs5_bye_top4"
    return "p4_plus_g6_bye_top4"


def committee_score(
    *,
    wins: float,
    strength: float,
    conf_key: str,
    sos: float = 1500.0,
    quality_wins: float = 0.0,
    season_progress: float = 1.0,
) -> float:
    """Approximate CFP committee résumé.

    Early weeks lean on preseason quality (cupcake 3-0s do not make the
    field). Late weeks add wins, SOS, and quality wins. SEC/Big Ten get
    the biggest brand bump — that is who the committee actually takes.
    """
    progress = min(1.0, max(0.0, float(season_progress)))
    losses = max(0.0, 12.0 * progress - float(wins)) if progress > 0 else 0.0
    quality = ((float(strength) - 1500.0) / 20.0) * (1.35 - 0.40 * progress)
    sos_comp = ((float(sos) - 1500.0) / 35.0) * (0.40 + 0.60 * progress)
    win_comp = (0.12 + 0.88 * progress) * float(wins)
    loss_pen = (0.90 * progress) * losses
    qwin_comp = (0.40 + 0.45 * progress) * float(quality_wins)
    if conf_key in {"sec", "big_ten"}:
        brand = 6.4
    elif conf_key in {"acc", "big_12"}:
        brand = 4.8
    elif conf_key == INDEPENDENT_KEY:
        brand = 5.1
    elif is_group(conf_key):
        brand = -5.2
    else:
        brand = 0.0
    return quality + win_comp + sos_comp + qwin_comp + brand - loss_pen


def champ_auto_score(
    *,
    wins: float,
    strength: float,
    conf_key: str,
) -> float:
    """Rank conference champions for the 5 auto bids.

    An 8-5 Power 4 champ (Duke 2025) should lose a slot to an 11-2 / 12-1
    Group of 5 champ (Tulane, James Madison). A 10-3 Power 4 champ
    (Clemson 2024) should keep the slot.
    """
    quality = (float(strength) - 1500.0) / 22.0
    losses = max(0.0, 13.0 - float(wins))
    return quality + 1.15 * float(wins) - 1.65 * losses + (
        2.4 if is_power(conf_key) else 0.0
    )


def regress_offseason(ratings: dict[str, float], *, factor: float = 0.70) -> dict[str, float]:
    """Pull leftover Elo toward 1500 so last year's leftovers do not lock the field."""
    return {team: 1500.0 + factor * (float(rating) - 1500.0) for team, rating in ratings.items()}


def select_playoff_indices(
    *,
    champs: dict[str, int],
    wins: list[float] | Any,
    strength: list[float] | Any,
    teams: list[str],
    team_conf: dict[str, str],
    sos: list[float] | None = None,
    quality_wins: list[float] | None = None,
    season: int = 2026,
    season_progress: float = 1.0,
) -> tuple[list[int], set[int]]:
    """Return seeded field indices and the auto-bid set."""
    n = len(teams)
    sos_arr = list(sos) if sos is not None else [1500.0] * n
    qwin_arr = list(quality_wins) if quality_wins is not None else [0.0] * n
    scores = [
        committee_score(
            wins=float(wins[i]),
            strength=float(strength[i]),
            conf_key=team_conf.get(teams[i], ""),
            sos=float(sos_arr[i]),
            quality_wins=float(qwin_arr[i]),
            season_progress=season_progress,
        )
        for i in range(n)
    ]

    def _rank_key(idx: int) -> tuple:
        return (-scores[idx], -float(wins[idx]), -float(strength[idx]), teams[idx])

    def _champ_key(idx: int) -> tuple:
        return (
            -champ_auto_score(
                wins=float(wins[idx]),
                strength=float(strength[idx]),
                conf_key=team_conf.get(teams[idx], ""),
            ),
            -float(wins[idx]),
            teams[idx],
        )

    rules = playoff_rules_year(season)
    champ_idxs = list(champs.values())
    auto: list[int] = []

    if rules == "p4_plus_g6_bye_top4":
        for key in ("sec", "big_ten", "big_12", "acc"):
            if key in champs:
                auto.append(champs[key])
        g6 = [
            i
            for i, team in enumerate(teams)
            if is_group(team_conf.get(team, ""))
        ]
        g6_ranked = sorted(g6, key=_champ_key)
        if g6_ranked and g6_ranked[0] not in auto:
            auto.append(g6_ranked[0])
    else:
        champ_ranked = sorted(champ_idxs, key=_champ_key)
        auto = champ_ranked[:AUTO_BIDS]

    auto = list(dict.fromkeys(auto))
    auto_set = set(auto)
    leftover = [i for i in range(n) if i not in auto_set]
    leftover_ranked = sorted(leftover, key=_rank_key)
    at_large = leftover_ranked[: PLAYOFF_FIELD - len(auto)]
    field = auto + at_large

    if rules == "champs5_bye_champs":
        champ_auto = [i for i in sorted(champ_idxs, key=_rank_key) if i in auto_set]
        byes = champ_auto[:BYE_SEEDS]
        rest = [i for i in sorted(field, key=_rank_key) if i not in set(byes)]
        field = byes + rest
    else:
        field.sort(key=_rank_key)

    return field[:PLAYOFF_FIELD], auto_set


def field_hit_rate(predicted: list[str], actual: list[str] | tuple[str, ...]) -> float:
    if not actual:
        return 0.0
    pred = {normalize(t) for t in predicted}
    act = {normalize(t) for t in actual}
    return len(pred & act) / float(len(act))


def normalize(name: str) -> str:
    from app.odds.cfb_team_aliases import normalize_team_name

    return normalize_team_name(name)


def misses(predicted: list[str], actual: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
    pred = [normalize(t) for t in predicted]
    act = [normalize(t) for t in actual]
    pred_set, act_set = set(pred), set(act)
    return {
        "false_in": sorted(pred_set - act_set),
        "false_out": sorted(act_set - pred_set),
    }
