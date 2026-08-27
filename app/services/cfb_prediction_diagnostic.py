"""Explain one CFB moneyline prediction from its actual pregame inputs."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any
import numpy as np
import pandas as pd

from app.features.cfb_pregame import build_features_for_slate
from app.models.cfb_baseline import PARQUET_PATH, load_games, load_model_artifact
from app.services.cfb_slate_predictions import cfb_season_end_year, _model_team_name
from app.services.schedule_cfb import get_cfb_schedule

PAIRS = {
    "elo_diff": ("elo_home_pre", "elo_away_pre"),
    "last5_win_pct_diff": ("home_last5_win_pct", "away_last5_win_pct"),
    "conf_win_pct_diff": ("home_conf_win_pct", "away_conf_win_pct"),
    "sp_plus_diff": ("home_sp_plus", "away_sp_plus"),
    "sp_offense_diff": ("home_sp_offense", "away_sp_offense"),
    "sp_defense_diff": ("home_sp_defense", "away_sp_defense"),
    "talent_diff": ("home_talent", "away_talent"),
    "returning_pct_diff": ("home_returning_pct", "away_returning_pct"),
    "returning_pass_pct_diff": ("home_returning_pass_pct", "away_returning_pass_pct"),
    "prior_fpi_diff": ("home_prior_fpi", "away_prior_fpi"),
    "srs_diff": ("home_srs", "away_srs"),
    "matchup_tier_diff": ("home_matchup_tier", "away_matchup_tier"),
}
REQUIRED_SOURCE_COLUMNS = ("neutral_site", "conference_game", "home_conference", "away_conference", "week")


def _value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value.item() if isinstance(value, (np.integer, np.floating)) else value


def _json_fetched_at(path) -> str | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("fetched_at")
        return str(value) if value else None
    except (OSError, ValueError, TypeError):
        return None


def _feature_freshness(
    feature: str,
    *,
    season: int,
    game_week: int,
    latest_prior_game: str | None,
    sp_available: bool,
) -> str:
    cache_root = PARQUET_PATH.parent
    if feature.startswith("sp_"):
        if not sp_available:
            return "unavailable for this pregame week; neutral zero used"
        meta_path = cache_root / "cfb_sp_plus_cache" / f"{season}_meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            meta = {}
        mode = meta.get("weekly_mode", "flat")
        prior_week = max(0, game_week - 1)
        if mode == "ok" and prior_week > 0:
            source_path = cache_root / "cfb_sp_plus_cache" / f"{season}_week_{prior_week}.json"
        else:
            source_path = cache_root / "cfb_sp_plus_cache" / f"{season}_preseason.json"
        fetched = _json_fetched_at(source_path)
        return f"cache fetched {fetched}" if fetched else f"cache file {source_path.name}"
    prior_kind = None
    prior_season = season
    if "talent" in feature:
        prior_kind = "talent"
    elif "returning" in feature:
        prior_kind = "returning"
    elif "prior_fpi" in feature:
        prior_kind = "fpi"
        prior_season = season - 1
    elif "coach" in feature:
        prior_kind = "coaches"
    if prior_kind:
        source_path = (
            cache_root / "cfb_priors_cache" / f"{prior_season}_{prior_kind}.json"
        )
        fetched = _json_fetched_at(source_path)
        return f"cache fetched {fetched}" if fetched else f"cache file {source_path.name}"
    history_tokens = (
        "elo", "season", "rest", "last5", "conf_win", "srs", "program"
    )
    if any(token in feature for token in history_tokens):
        return (
            f"latest eligible prior result {latest_prior_game}"
            if latest_prior_game
            else "no eligible prior result; pregame default used"
        )
    if any(token in feature for token in ("neutral", "home_field", "conference_game", "matchup", "fcs")):
        return "saved game metadata for the listed game/date"
    return "computed at diagnostic time from the listed pregame inputs"


def _source(feature: str) -> str:
    groups = (
        ("elo", "cfb_games.parquet results strictly before kickoff"),
        ("season", "cfb_games.parquet chronological team results"),
        ("rest", "cfb_games.parquet prior game dates"),
        ("sp_", "CFBD SP+ cache selected by season/week"),
        ("talent", "CFBD season prior cache"),
        ("returning", "CFBD season prior cache"),
        ("prior_fpi", "prior-season CFBD FPI cache"),
        ("coach", "CFBD season coach cache"),
        ("srs", "pregame score-differential tracker"),
        ("program", "pregame home-results tracker"),
        ("matchup", "conference classification from ingest"),
        ("conference", "conference metadata and prior results"),
        ("week", "ingest week or date-derived calendar week"),
        ("neutral", "CFBD/ESPN game metadata"),
        ("home_field", "neutral-site flag"),
        ("prior_weight", "date/week-derived prior blend"),
        ("blended", "pregame prior and SRS blend"),
    )
    return next((source for token, source in groups if token in feature), "derived pregame feature")


def _game_row(game_id: str, game_date: date | None) -> tuple[pd.Series, dict[str, Any], pd.DataFrame]:
    history = load_games()
    found = history[history["game_id"].astype(str) == str(game_id)]
    if not found.empty:
        row = found.iloc[0].copy()
        actual = {"home_score": int(row.home_score), "away_score": int(row.away_score),
                  "home_win": int(row.home_win), "winner": row.home_team if int(row.home_win) else row.away_team}
        return row, actual, history
    if game_date is None:
        raise KeyError(f"Unknown CFB game_id {game_id}; provide --date for a scheduled game")
    schedule = get_cfb_schedule(game_date)
    game = next((g for g in schedule.get("games", []) if str(g.get("game_id")) == str(game_id)), None)
    if game is None:
        raise KeyError(f"CFB game_id {game_id} not found on {game_date.isoformat()}")
    canonical = tuple(sorted(set(history.home_team.astype(str)) | set(history.away_team.astype(str)), key=len, reverse=True))
    row = pd.Series({
        "game_id": str(game_id), "date": game_date.isoformat(), "season": cfb_season_end_year(game_date),
        "home_team": _model_team_name(game, "home", canonical), "away_team": _model_team_name(game, "away", canonical),
        "neutral_site": int(bool(game.get("neutral_site"))), "week": int(game.get("week") or 0),
        "conference_game": int(bool(game.get("conference_game"))),
        "home_conference": str(game.get("home_conference") or ""), "away_conference": str(game.get("away_conference") or ""),
    })
    return row, {}, history


def diagnose_cfb_prediction(game_id: str, game_date: date | None = None) -> dict[str, Any]:
    row, actual, history = _game_row(game_id, game_date)
    artifact = load_model_artifact()
    cols = list(artifact["feature_columns"])
    rest_fill = float(artifact.get("rest_fill", 7.0))
    prepared = build_features_for_slate(pd.DataFrame([row.to_dict()]), rest_fill=rest_fill, history_df=history)
    if len(prepared) != 1:
        raise ValueError(f"Expected one feature row for {game_id}, got {len(prepared)}")
    feat = prepared.iloc[0]
    pipe = artifact["model"]
    scaler, classifier = pipe.named_steps["scaler"], pipe.named_steps["clf"]
    values = feat[cols].astype(float).to_numpy().reshape(1, -1)
    normalized = scaler.transform(values)[0]
    weights = classifier.coef_[0]
    contributions = normalized * weights
    intercept = float(classifier.intercept_[0])
    home_logit = float(intercept + contributions.sum())
    base_home = float(pipe.predict_proba(values)[0, 1])
    platt = artifact.get("platt_calibrator")
    final_home = float(platt.transform(np.array([base_home]))[0]) if platt is not None else base_home
    persisted_columns = set(pd.read_parquet(PARQUET_PATH).columns) if PARQUET_PATH.exists() else set(history.columns)
    missing_columns = [c for c in REQUIRED_SOURCE_COLUMNS if c not in persisted_columns]
    day = pd.to_datetime(row["date"])
    prior = history[pd.to_datetime(history["date"]) < day]
    latest_prior_game = (
        None
        if prior.empty
        else pd.to_datetime(prior["date"]).max().date().isoformat()
    )
    sp_available = bool(feat.get("sp_plus_available", 0))
    details = []
    for idx, name in enumerate(cols):
        pair = PAIRS.get(name)
        home_raw = _value(feat.get(pair[0])) if pair else (_value(feat.get(name)) if name.startswith("home_") else None)
        away_raw = _value(feat.get(pair[1])) if pair else (_value(feat.get(name)) if name.startswith("away_") else None)
        raw = float(values[0, idx])
        metadata_default = name in {"neutral_site", "home_field_active", "conference_game", "matchup_tier_diff", "is_fcs_away"} and bool(missing_columns)
        neutral_default = ("win_pct" in name and raw == .5) or ("rest" in name and raw == rest_fill)
        source_missing = name.startswith("sp_") and not sp_available
        paired_source_missing = (
            pair is not None
            and name in {"talent_diff", "returning_pct_diff", "returning_pass_pct_diff", "prior_fpi_diff"}
            and home_raw == 0.0
            and away_raw == 0.0
        )
        defaulted = metadata_default or neutral_default or source_missing or paired_source_missing
        details.append({
            "feature": name, "home_raw": home_raw, "away_raw": away_raw, "raw_value": raw,
            "normalized_value": float(normalized[idx]), "difference": raw if "diff" in name else None,
            "direction": "raises home probability" if weights[idx] > 0 else "raises away probability" if weights[idx] < 0 else "no fitted direction",
            "weight": float(weights[idx]), "home_logit_contribution": float(contributions[idx]),
            "away_logit_contribution": float(-contributions[idx]),
            "defaulted_or_missing": defaulted,
            "default_reason": (
                "required metadata absent from saved cfb_games.parquet"
                if metadata_default
                else "SP+ snapshot unavailable under leakage-safe week policy"
                if source_missing
                else "both team prior values absent; neutral zero difference used"
                if paired_source_missing
                else "neutral/median pregame fallback; may also be legitimate"
                if neutral_default
                else None
            ),
            "source": _source(name),
            "source_freshness": _feature_freshness(
                name,
                season=int(row["season"]),
                game_week=int(row.get("week", 0) or 0),
                latest_prior_game=latest_prior_game,
                sp_available=sp_available,
            ),
        })
    pick_home = final_home >= .5
    return {
        "game": {"game_id": str(game_id), "date": str(row["date"])[:10], "season": int(row["season"]),
                 "week": int(row.get("week", 0) or 0), "home_team": str(row["home_team"]),
                 "away_team": str(row["away_team"]), "neutral_site": bool(row.get("neutral_site", 0)),
                 "actual_result": actual or None},
        "prediction": {"predicted_winner": str(row["home_team"] if pick_home else row["away_team"]),
                       "predicted_side": "home" if pick_home else "away", "raw_home_logit": home_logit,
                       "raw_away_logit": -home_logit, "base_home_probability": base_home,
                       "home_probability": final_home, "away_probability": 1-final_home,
                       "confidence": max(final_home, 1-final_home), "intercept": intercept,
                       "model_version": artifact.get("model_version"), "feature_set": artifact.get("feature_set")},
        "features": details,
        "data_quality": {"missing_required_dataset_columns": missing_columns,
                         "defaulted_feature_count": sum(int(x["defaulted_or_missing"]) for x in details),
                         "history_source": "data/processed/cfb_games.parquet",
                         "history_latest_prior_game": latest_prior_game,
                         "history_rows_before_game": len(prior)},
    }
