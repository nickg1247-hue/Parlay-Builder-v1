"""Human-readable MLB game model explanations from pregame features."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.models.mlb_baseline import attach_elo_for_slate
from app.parlay.slate import (
    build_slate_dataframe,
    build_slate_from_history,
    fetch_mlb_schedule_day,
    filter_board_games,
)


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value: float | None, digits: int = 1) -> str | None:
    if value is None:
        return None
    return f"{value * 100:.{digits}f}%"


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def feature_row_for_game(
    game_id: str,
    game_date: date,
    *,
    use_cache: bool = False,
    board_row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pregame feature dict for one game (same pipeline as slate scoring)."""
    gid = str(game_id)
    if use_cache:
        slate = build_slate_from_history(game_date)
    else:
        api_games = filter_board_games(fetch_mlb_schedule_day(game_date), game_date)
        api_games = [g for g in api_games if str(g.get("gamePk")) == gid]
        if api_games:
            slate = build_slate_dataframe(game_date, api_games=api_games)
        else:
            slate = build_slate_from_history(game_date)

    if slate.empty:
        return None

    match = slate[slate["game_id"].astype(str) == gid]
    if match.empty and board_row:
        home_team = board_row.get("home_team")
        away_team = board_row.get("away_team")
        if home_team and away_team:
            match = slate[
                (slate["home_team"] == home_team) & (slate["away_team"] == away_team)
            ]
    if match.empty:
        return None

    row_df = attach_elo_for_slate(match.iloc[[0]].copy())
    row = row_df.iloc[0].to_dict()
    row["home_team"] = board_row.get("home_team") if board_row else match.iloc[0]["home_team"]
    row["away_team"] = board_row.get("away_team") if board_row else match.iloc[0]["away_team"]
    return row


def _factor(
    label: str,
    home_val: float | None,
    away_val: float | None,
    *,
    home_team: str,
    away_team: str,
    lower_is_better: bool = False,
    min_gap: float = 0.0,
    home_fmt: str | None = None,
    away_fmt: str | None = None,
) -> dict[str, Any] | None:
    if home_val is None or away_val is None:
        return None
    gap = abs(home_val - away_val)
    if gap <= min_gap:
        return {
            "factor": label,
            "home": home_fmt or _fmt(home_val),
            "away": away_fmt or _fmt(away_val),
            "edge": "neutral",
            "home_text": None,
            "away_text": None,
            "detail": (
                f"{label}: {home_team} {home_fmt or _fmt(home_val)} vs "
                f"{away_team} {away_fmt or _fmt(away_val)} — even."
            ),
        }

    home_better = home_val < away_val if lower_is_better else home_val > away_val
    edge = "home" if home_better else "away"
    better = home_team if home_better else away_team
    h_display = home_fmt or _fmt(home_val)
    a_display = away_fmt or _fmt(away_val)

    text = (
        f"{label}: {home_team} {h_display} vs {away_team} {a_display} — "
        f"edge to {better}."
    )
    return {
        "factor": label,
        "home": h_display,
        "away": a_display,
        "edge": edge,
        "home_text": text if edge == "home" else None,
        "away_text": text if edge == "away" else None,
        "detail": text,
    }


def build_mlb_factor_comparison(
    feats: dict[str, Any],
    home_team: str,
    away_team: str,
) -> list[dict[str, Any]]:
    """Rule-based factor table shared by explanations and pick reconciliation."""
    home_wp10 = _num(feats.get("home_last10_win_pct"))
    away_wp10 = _num(feats.get("away_last10_win_pct"))
    home_rd10 = _num(feats.get("home_last10_run_diff"))
    away_rd10 = _num(feats.get("away_last10_run_diff"))

    factors: list[dict[str, Any] | None] = [
        _factor(
            "Starting pitcher ERA (season)",
            _num(feats.get("home_pitcher_era")),
            _num(feats.get("away_pitcher_era")),
            home_team=home_team,
            away_team=away_team,
            lower_is_better=True,
            min_gap=0.15,
        ),
        _factor(
            "Pitcher form (last 5 starts ERA)",
            _num(feats.get("home_pitcher_era_l5")),
            _num(feats.get("away_pitcher_era_l5")),
            home_team=home_team,
            away_team=away_team,
            lower_is_better=True,
            min_gap=0.2,
        ),
        _factor(
            "Recent form (last 10 win %)",
            home_wp10,
            away_wp10,
            home_team=home_team,
            away_team=away_team,
            min_gap=0.03,
            home_fmt=_pct(home_wp10),
            away_fmt=_pct(away_wp10),
        ),
        _factor(
            "Recent scoring (last 10 run diff/game)",
            home_rd10,
            away_rd10,
            home_team=home_team,
            away_team=away_team,
            min_gap=0.3,
        ),
        _factor(
            "Season win %",
            _num(feats.get("home_season_win_pct")),
            _num(feats.get("away_season_win_pct")),
            home_team=home_team,
            away_team=away_team,
            min_gap=0.03,
            home_fmt=_pct(_num(feats.get("home_season_win_pct"))),
            away_fmt=_pct(_num(feats.get("away_season_win_pct"))),
        ),
        _factor(
            "Home/away split win %",
            _num(feats.get("home_home_split_win_pct")),
            _num(feats.get("away_away_split_win_pct")),
            home_team=home_team,
            away_team=away_team,
            min_gap=0.03,
            home_fmt=_pct(_num(feats.get("home_home_split_win_pct"))),
            away_fmt=_pct(_num(feats.get("away_away_split_win_pct"))),
        ),
        _factor(
            "Bullpen ERA (last 14 days)",
            _num(feats.get("home_bullpen_era_14d")),
            _num(feats.get("away_bullpen_era_14d")),
            home_team=home_team,
            away_team=away_team,
            lower_is_better=True,
            min_gap=0.25,
        ),
    ]

    home_elo = _num(feats.get("elo_home_pre"))
    away_elo = _num(feats.get("elo_away_pre"))
    if home_elo is not None and away_elo is not None:
        elo_diff = home_elo - away_elo
        if abs(elo_diff) >= 15:
            if elo_diff > 0:
                factors.append(
                    {
                        "factor": "Elo rating",
                        "home": _fmt(home_elo, 0),
                        "away": _fmt(away_elo, 0),
                        "edge": "home",
                        "home_text": (
                            f"Elo strength favors {home_team} "
                            f"({home_elo:.0f} vs {away_elo:.0f}, incl. home field)."
                        ),
                        "away_text": None,
                        "detail": (
                            f"Elo strength favors {home_team} "
                            f"({home_elo:.0f} vs {away_elo:.0f}, incl. home field)."
                        ),
                    }
                )
            else:
                factors.append(
                    {
                        "factor": "Elo rating",
                        "home": _fmt(home_elo, 0),
                        "away": _fmt(away_elo, 0),
                        "edge": "away",
                        "home_text": None,
                        "away_text": (
                            f"Elo strength favors {away_team} "
                            f"({away_elo:.0f} vs {home_elo:.0f})."
                        ),
                        "detail": (
                            f"Elo strength favors {away_team} "
                            f"({away_elo:.0f} vs {home_elo:.0f})."
                        ),
                    }
                )

    home_rest = _num(feats.get("home_rest_days"))
    away_rest = _num(feats.get("away_rest_days"))
    if home_rest is not None and away_rest is not None and abs(home_rest - away_rest) >= 2:
        if home_rest > away_rest:
            factors.append(
                {
                    "factor": "Rest days",
                    "home": _fmt(home_rest, 0),
                    "away": _fmt(away_rest, 0),
                    "edge": "home",
                    "home_text": (
                        f"{home_team} has more rest ({int(home_rest)} days vs {int(away_rest)})."
                    ),
                    "away_text": None,
                    "detail": (
                        f"{home_team} has more rest ({int(home_rest)} days vs {int(away_rest)})."
                    ),
                }
            )
        else:
            factors.append(
                {
                    "factor": "Rest days",
                    "home": _fmt(home_rest, 0),
                    "away": _fmt(away_rest, 0),
                    "edge": "away",
                    "home_text": None,
                    "away_text": (
                        f"{away_team} has more rest ({int(away_rest)} days vs {int(home_rest)})."
                    ),
                    "detail": (
                        f"{away_team} has more rest ({int(away_rest)} days vs {int(home_rest)})."
                    ),
                }
            )

    return [
        {
            "factor": f["factor"],
            "home": f["home"],
            "away": f["away"],
            "edge": f["edge"],
            "detail": f.get("detail"),
        }
        for f in factors
        if f
    ]


def build_totals_explanation(
    feats: dict[str, Any],
    board_row: dict[str, Any] | None,
    home_team: str,
    away_team: str,
) -> dict[str, Any] | None:
    if not board_row:
        return None
    expected = _num(board_row.get("expected_total_runs"))
    line = _num(board_row.get("ou_line"))
    pick = board_row.get("totals_pick")
    if expected is None and line is None and not pick:
        return None

    bullets: list[str] = []
    pf = _num(feats.get("park_factor_runs"))
    if pf is not None:
        if pf >= 1.05:
            bullets.append(
                f"{home_team}'s park runs hot (factor {pf:.2f}) — more run environment than average."
            )
        elif pf <= 0.95:
            bullets.append(
                f"{home_team}'s park suppresses runs (factor {pf:.2f})."
            )

    home_rd = _num(feats.get("home_season_run_diff"))
    away_rd = _num(feats.get("away_season_run_diff"))
    if home_rd is not None and away_rd is not None:
        combined = home_rd + away_rd
        if combined >= 0.5:
            bullets.append(
                "Both offenses profile above average on season run differential."
            )
        elif combined <= -0.5:
            bullets.append(
                "Both teams profile below average on season run differential."
            )

    home_era = _num(feats.get("home_pitcher_era"))
    away_era = _num(feats.get("away_pitcher_era"))
    if home_era is not None and away_era is not None:
        avg_era = (home_era + away_era) / 2.0
        if avg_era >= 4.6:
            bullets.append(
                f"Starting pitching matchup is soft (combined ERA ~{avg_era:.2f})."
            )
        elif avg_era <= 3.6:
            bullets.append(
                f"Strong starting pitching (combined ERA ~{avg_era:.2f}) caps run upside."
            )

    if expected is not None and line is not None:
        bullets.append(
            f"Model expects {expected:.1f} total runs vs sportsbook line {line:.1f}."
        )
    elif expected is not None:
        bullets.append(f"Model expects {expected:.1f} total runs in this matchup.")

    summary = None
    if pick and expected is not None and line is not None:
        summary = f"Totals lean {pick} — model {expected:.1f} runs vs line {line:.1f}."
    elif pick:
        summary = f"Totals lean {pick}."

    return {
        "expected_runs": expected,
        "ou_line": line,
        "pick": pick,
        "model_prob_over": board_row.get("model_prob_over"),
        "market_prob_over": board_row.get("market_prob_over"),
        "summary": summary,
        "bullets": bullets,
    }


def build_mlb_game_explanation(
    game_id: str,
    game_date: date,
    board_row: dict[str, Any] | None,
    *,
    use_cache: bool = False,
) -> dict[str, Any] | None:
    """Explain model win lean and totals using pregame features."""
    if not board_row:
        return None

    feats = feature_row_for_game(
        game_id, game_date, use_cache=use_cache, board_row=board_row
    )
    if not feats:
        return None

    home_team = str(board_row.get("home_team") or feats.get("home_team") or "Home")
    away_team = str(board_row.get("away_team") or feats.get("away_team") or "Away")
    prob_home = _num(board_row.get("model_prob_home"))
    prob_home_raw = _num(board_row.get("model_prob_home_raw")) or prob_home
    pick_team = board_row.get("model_pick_team")
    pick_side = board_row.get("model_pick_side")
    if not pick_team and prob_home is not None:
        pick_side = "home" if prob_home >= 0.5 else "away"
        pick_team = home_team if pick_side == "home" else away_team

    comparison = build_mlb_factor_comparison(feats, home_team, away_team)
    why_home = []
    why_away = []
    for item in comparison:
        edge = item.get("edge")
        detail = item.get("detail")
        if not detail:
            continue
        if edge == "home":
            why_home.append(detail)
        elif edge == "away":
            why_away.append(detail)

    from app.services.mlb_pick_reconcile import count_factor_votes
    from app.models.mlb_ensemble import (
        load_ensemble_artifact,
        predict_ensemble_components,
        is_ensemble_artifact,
    )

    votes = count_factor_votes(comparison)
    majority_side = None
    if votes["home"] > votes["away"]:
        majority_side = "home"
    elif votes["away"] > votes["home"]:
        majority_side = "away"
    majority_team = (
        home_team
        if majority_side == "home"
        else away_team
        if majority_side == "away"
        else None
    )

    ensemble_components: dict[str, float] | None = None
    try:
        art = load_ensemble_artifact()
        if art is not None and is_ensemble_artifact(art):
            import pandas as pd

            comps = predict_ensemble_components(pd.DataFrame([feats]), art)
            ensemble_components = {
                "logistic": round(float(comps["logistic_prob_home"][0]), 4),
                "gbc": round(float(comps["gbc_prob_home"][0]), 4),
                "elo": round(float(comps["elo_prob_home"][0]), 4),
                "ensemble_raw": round(float(comps["ensemble_raw_prob_home"][0]), 4),
                "ensemble": round(float(comps["ensemble_prob_home"][0]), 4),
            }
    except (FileNotFoundError, ValueError, KeyError):
        ensemble_components = None

    pick_reconciled = bool(board_row.get("pick_reconciled"))
    alignment_note = None
    if pick_reconciled and majority_team:
        alignment_note = (
            f"Pick aligned with pregame factor consensus ({majority_team}, "
            f"{votes['away']}–{votes['home']} away–home edges). "
            f"Raw ensemble had {prob_home_raw * 100:.1f}% home win before adjustment."
        )
    elif majority_team and pick_team and majority_team != pick_team:
        alignment_note = (
            f"Pregame factors lean {majority_team} ({votes['away']}–{votes['home']} "
            f"away–home edges) but the model still favors {pick_team}. "
            "Treat as a low-confidence lean."
        )

    pick_prob = _num(board_row.get("model_pick_prob"))
    if pick_prob is None and prob_home is not None and pick_side:
        pick_prob = prob_home if pick_side == "home" else 1.0 - prob_home

    summary = None
    if pick_team and pick_prob is not None:
        conf = board_row.get("model_confidence") or "model lean"
        summary = (
            f"Model leans {pick_team} ({pick_prob * 100:.1f}% win) — {conf}."
        )
    elif pick_team:
        summary = f"Model leans {pick_team}."

    return {
        "summary": summary,
        "home_team": home_team,
        "away_team": away_team,
        "model_pick_team": pick_team,
        "model_pick_side": pick_side,
        "model_prob_home": prob_home,
        "home_win_pct": round(prob_home * 100, 1) if prob_home is not None else None,
        "away_win_pct": round((1 - prob_home) * 100, 1) if prob_home is not None else None,
        "why_home": why_home,
        "why_away": why_away,
        "factor_comparison": comparison,
        "factor_votes": votes,
        "factor_majority_team": majority_team,
        "pick_reconciled": pick_reconciled,
        "model_prob_home_raw": prob_home_raw,
        "alignment_note": alignment_note,
        "ensemble_components": ensemble_components,
        "totals": build_totals_explanation(feats, board_row, home_team, away_team),
        "disclaimer": (
            "Factor table summarizes visible pregame inputs; ensemble also uses "
            "pitcher L3/WHIP, bullpen workload, and calibrated blend weights."
        ),
    }
