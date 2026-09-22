"""Opponent-adjusted team power ratings.

Offense and defense are points versus a league-average opponent. Schedule
strength is the gap between those ratings and the raw scoring averages, so it
is shown on the page and is not added again. Recent games get more weight
inside the season sample. Last year's rating is blended in and fades as games
accumulate. Factors without a feed are listed and left out of the score.
"""

from __future__ import annotations

from dataclasses import dataclass, field


HALF_LIFE_GAMES = 4.0
SOLVE_ITERATIONS = 40


@dataclass(frozen=True)
class RawGame:
    team: str
    opponent: str
    points_for: float
    points_against: float
    is_home: bool
    neutral: bool
    when: float
    scale: float = 1.0
    win: int = 0


@dataclass
class SideRating:
    offense: float = 0.0
    defense: float = 0.0
    raw_offense: float = 0.0
    raw_defense: float = 0.0
    games: int = 0
    wins: int = 0


@dataclass
class ScoreParts:
    power_tenths: int
    offense_tenths: int
    defense_tenths: int
    offense_season_tenths: int
    defense_season_tenths: int
    prior_tenths: int
    prior_offense_tenths: int
    prior_defense_tenths: int
    schedule_tenths: int
    recent_tenths: int
    prior_part_tenths: list[tuple[str, int]] = field(default_factory=list)


def prior_weight_for_games(games_played: int, *, late_weight: float, late_games: int) -> float:
    """Mostly prior early. Shrinks toward late_weight as results accumulate."""
    played = max(0, int(games_played))
    if played <= 0:
        return 0.85
    if played <= 3:
        return 0.70
    if played >= late_games:
        return late_weight
    span = late_games - 3
    return 0.70 - (played - 3) * ((0.70 - late_weight) / span)


def cap_scores(points_for: float, points_against: float, cap: float) -> tuple[float, float]:
    """Pull a lopsided margin in to cap while keeping the game total."""
    margin = points_for - points_against
    if abs(margin) <= cap:
        return points_for, points_against
    total = points_for + points_against
    signed = cap if margin > 0 else -cap
    return (total + signed) / 2.0, (total - signed) / 2.0


def _neutralize_home_field(
    game: RawGame,
    hfa: float,
) -> tuple[float, float]:
    points_for = game.points_for
    points_against = game.points_against
    if not game.neutral and hfa:
        shift = hfa / 2.0
        if game.is_home:
            points_for -= shift
            points_against += shift
        else:
            points_for += shift
            points_against -= shift
    points_for = max(0.0, points_for)
    points_against = max(0.0, points_against)
    return points_for, points_against


@dataclass
class _Obs:
    team: str
    opponent: str
    points_for: float
    points_against: float
    weight: float
    win: int


def _observations(
    games: list[RawGame],
    *,
    hfa: float,
    margin_cap: float,
    half_life: float,
    recency: bool,
) -> list[_Obs]:
    prepared: dict[str, list[tuple[float, RawGame, float, float]]] = {}
    for game in games:
        points_for, points_against = _neutralize_home_field(game, hfa)
        points_for, points_against = cap_scores(points_for, points_against, margin_cap)
        prepared.setdefault(game.team, []).append((game.when, game, points_for, points_against))

    observations: list[_Obs] = []
    for rows in prepared.values():
        rows.sort(key=lambda item: item[0])
        last_index = len(rows) - 1
        for index, (_, game, points_for, points_against) in enumerate(rows):
            age = (last_index - index) if recency else 0
            decay = 0.5 ** (age / half_life) if recency else 1.0
            weight = decay * max(0.0, game.scale)
            if weight <= 0:
                continue
            observations.append(
                _Obs(
                    team=game.team,
                    opponent=game.opponent,
                    points_for=points_for,
                    points_against=points_against,
                    weight=weight,
                    win=game.win,
                )
            )
    return observations


def rate_games(
    games: list[RawGame],
    ranking_teams: set[str],
    *,
    hfa: float,
    margin_cap: float,
    half_life: float = HALF_LIFE_GAMES,
    recency: bool = True,
) -> dict[str, SideRating]:
    """Iterative opponent adjustment. Higher offense and defense are better."""
    observations = _observations(
        games,
        hfa=hfa,
        margin_cap=margin_cap,
        half_life=half_life,
        recency=recency,
    )
    teams = set(ranking_teams)
    for obs in observations:
        teams.add(obs.team)
        teams.add(obs.opponent)

    offense = {team: 0.0 for team in teams}
    defense = {team: 0.0 for team in teams}
    weight_total = sum(obs.weight for obs in observations)
    average_points = (
        sum(obs.weight * obs.points_for for obs in observations) / weight_total
        if weight_total
        else 0.0
    )
    played = {team for obs in observations for team in (obs.team,)}
    center_on = [team for team in ranking_teams if team in played] or list(played)

    for _ in range(SOLVE_ITERATIONS):
        previous_offense = dict(offense)
        previous_defense = dict(defense)
        off_num = {team: 0.0 for team in teams}
        off_den = {team: 0.0 for team in teams}
        def_num = {team: 0.0 for team in teams}
        def_den = {team: 0.0 for team in teams}
        for obs in observations:
            off_num[obs.team] += obs.weight * (
                obs.points_for - (average_points - previous_defense[obs.opponent])
            )
            off_den[obs.team] += obs.weight
            def_num[obs.team] += obs.weight * (
                (average_points + previous_offense[obs.opponent]) - obs.points_against
            )
            def_den[obs.team] += obs.weight
        for team in teams:
            if off_den[team]:
                offense[team] = off_num[team] / off_den[team]
            if def_den[team]:
                defense[team] = def_num[team] / def_den[team]
        if center_on:
            off_mean = sum(offense[team] for team in center_on) / len(center_on)
            def_mean = sum(defense[team] for team in center_on) / len(center_on)
            for team in center_on:
                offense[team] -= off_mean
                defense[team] -= def_mean

    raw_off_num = {team: 0.0 for team in teams}
    raw_off_den = {team: 0.0 for team in teams}
    raw_def_num = {team: 0.0 for team in teams}
    raw_def_den = {team: 0.0 for team in teams}
    games_played = {team: 0 for team in teams}
    wins = {team: 0 for team in teams}
    for obs in observations:
        raw_off_num[obs.team] += obs.weight * obs.points_for
        raw_off_den[obs.team] += obs.weight
        raw_def_num[obs.team] += obs.weight * obs.points_against
        raw_def_den[obs.team] += obs.weight
        games_played[obs.team] += 1
        wins[obs.team] += int(obs.win)

    raw_offense = {
        team: (raw_off_num[team] / raw_off_den[team] - average_points) if raw_off_den[team] else 0.0
        for team in teams
    }
    raw_defense = {
        team: (average_points - raw_def_num[team] / raw_def_den[team]) if raw_def_den[team] else 0.0
        for team in teams
    }
    if center_on:
        raw_off_mean = sum(raw_offense[team] for team in center_on) / len(center_on)
        raw_def_mean = sum(raw_defense[team] for team in center_on) / len(center_on)
        for team in center_on:
            raw_offense[team] -= raw_off_mean
            raw_defense[team] -= raw_def_mean

    return {
        team: SideRating(
            offense=offense.get(team, 0.0),
            defense=defense.get(team, 0.0),
            raw_offense=raw_offense.get(team, 0.0),
            raw_defense=raw_defense.get(team, 0.0),
            games=games_played.get(team, 0),
            wins=wins.get(team, 0),
        )
        for team in ranking_teams
    }


def tenths(value: float) -> int:
    return int(round(float(value) * 10))


def from_tenths(value: int) -> float:
    return value / 10.0


def fit_parts(target: int, parts: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Make ingredient tenths add up to the parent line."""
    if not parts:
        return []
    fitted = list(parts)
    delta = target - sum(points for _, points in fitted)
    index = max(range(len(fitted)), key=lambda i: abs(fitted[i][1]))
    key, points = fitted[index]
    fitted[index] = (key, points + delta)
    return fitted


def compose_score(
    season: SideRating,
    flat: SideRating,
    *,
    prior_offense: float,
    prior_defense: float,
    prior_weight: float,
    prior_parts: list[tuple[str, float]],
) -> ScoreParts:
    weight = min(1.0, max(0.0, prior_weight))
    offense_season = tenths((1.0 - weight) * season.offense)
    defense_season = tenths((1.0 - weight) * season.defense)
    prior_offense_t = tenths(weight * prior_offense)
    prior_defense_t = tenths(weight * prior_defense)
    prior_total = prior_offense_t + prior_defense_t
    part_tenths = [(key, tenths(weight * value)) for key, value in prior_parts]
    part_tenths = fit_parts(prior_total, part_tenths)
    schedule = tenths(
        (season.offense - season.raw_offense) + (season.defense - season.raw_defense)
    )
    recent = tenths(
        (season.offense + season.defense) - (flat.offense + flat.defense)
    )
    return ScoreParts(
        power_tenths=offense_season + defense_season + prior_total,
        offense_tenths=offense_season + prior_offense_t,
        defense_tenths=defense_season + prior_defense_t,
        offense_season_tenths=offense_season,
        defense_season_tenths=defense_season,
        prior_tenths=prior_total,
        prior_offense_tenths=prior_offense_t,
        prior_defense_tenths=prior_defense_t,
        schedule_tenths=schedule,
        recent_tenths=recent,
        prior_part_tenths=part_tenths,
    )


def rank_map(values: dict[str, float]) -> dict[str, int]:
    ordered = sorted(values, key=lambda team: (-values[team], team))
    return {team: index for index, team in enumerate(ordered, start=1)}


def football_season(as_of) -> int:
    """August through December is the new season. January and February finish the previous one."""
    return as_of.year if as_of.month >= 8 else as_of.year - 1


def schedule_needs_refresh(games: list[dict], as_of) -> bool:
    """True when a game before today is still stored as unplayed."""
    past = [game for game in games if str(game.get("date") or "") < as_of.isoformat()]
    if not past:
        return False
    completed = [
        game
        for game in past
        if game.get("completed") and game.get("home_score") is not None and not game.get("tie")
    ]
    if not completed:
        return True
    return max(str(game.get("date") or "") for game in past) > max(
        str(game.get("date") or "") for game in completed
    )


PRIOR_PART_COPY: dict[str, tuple[str, str]] = {
    "last_season": (
        "Last season strength",
        "Opponent-adjusted offense and defense from last season. Newer games from that season count more.",
    ),
    "preseason": (
        "Preseason results",
        "This year's preseason games, blended lightly with last season.",
    ),
    "talent": (
        "Roster talent",
        "Preseason roster talent. It matters most before the current team has played.",
    ),
    "returning": (
        "Returning and transfer production",
        "Prior production from players on this season's roster.",
    ),
    "returning_pass": (
        "Returning passing production",
        "Returning quarterback and passing production. Weekly starter changes are not in this rating yet.",
    ),
    "coaching": (
        "Coaching continuity",
        "Same head coach as last season is a small preseason bump. A new coach is a small penalty.",
    ),
}


def points_label(tenths_value: int) -> float:
    return from_tenths(tenths_value)


def assemble_subpoints(
    parts: ScoreParts,
    *,
    team_count: int,
    offense_rank: int,
    defense_rank: int,
    prior_weight: float,
    games_played: int,
) -> list[dict]:
    """Counted lines sum to power. Schedule and recency are already inside efficiency."""
    prior_pct = int(round(prior_weight * 100))
    season_pct = 100 - prior_pct
    prior_bits = []
    for key, part_tenths in parts.prior_part_tenths:
        label, detail = PRIOR_PART_COPY.get(key, (key, ""))
        prior_bits.append(
            {
                "key": key,
                "label": label,
                "points": points_label(part_tenths),
                "detail": detail,
            }
        )
    return [
        {
            "key": "offense",
            "label": "Offensive efficiency",
            "points": points_label(parts.offense_season_tenths),
            "counted": True,
            "detail": (
                f"Opponent-adjusted points scored. This is {season_pct}% of the "
                f"season offense. Offensive rank {offense_rank} of {team_count} "
                f"also includes the offensive share of the prior ({points_label(parts.prior_offense_tenths):+.1f})."
            ),
        },
        {
            "key": "defense",
            "label": "Defensive efficiency",
            "points": points_label(parts.defense_season_tenths),
            "counted": True,
            "detail": (
                f"Opponent-adjusted points prevented. This is {season_pct}% of the "
                f"season defense. Defensive rank {defense_rank} of {team_count} "
                f"also includes the defensive share of the prior ({points_label(parts.prior_defense_tenths):+.1f})."
            ),
        },
        {
            "key": "prior",
            "label": "Prior team strength",
            "points": points_label(parts.prior_tenths),
            "counted": True,
            "detail": (
                f"{prior_pct}% of the rating still comes from before this season's results, "
                f"after {games_played} current-season game{'s' if games_played != 1 else ''}."
            ),
            "parts": prior_bits,
        },
        {
            "key": "schedule",
            "label": "Schedule strength",
            "points": points_label(parts.schedule_tenths),
            "counted": False,
            "detail": (
                "How far opponent quality moved this season's offense and defense. "
                "Already inside those efficiency numbers, so it is not added again."
            ),
        },
        {
            "key": "recent",
            "label": "Recent performance",
            "points": points_label(parts.recent_tenths),
            "counted": False,
            "detail": (
                "Newer games count more, with a four-game half-life, and the rest of "
                "the season still counts. This is how far that moved the season rating "
                "versus weighting every game the same. Already inside efficiency."
            ),
        },
    ]


def zscores(values: dict[str, float]) -> dict[str, float]:
    if len(values) < 2:
        return {key: 0.0 for key in values}
    mean = sum(values.values()) / len(values)
    variance = sum((value - mean) ** 2 for value in values.values()) / len(values)
    std = variance ** 0.5
    if std < 1e-6:
        return {key: 0.0 for key in values}
    return {key: (value - mean) / std for key, value in values.items()}
