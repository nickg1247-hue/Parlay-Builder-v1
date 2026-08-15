"""Walk-forward search for a stronger NFL moneyline setup. No API calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PROJECT_ROOT
from app.features.nfl_pregame import FEATURE_COLUMNS, FEATURE_COLUMNS_V2, build_features_for_history
from app.models.nfl_baseline import compute_metrics, load_games, predict_elo, train_logistic
from app.models.platt_calibration import PlattCalibrator

OUT = PROJECT_ROOT / "data" / "processed" / "nfl_moneyline_tune.json"
SEASONS = (2023, 2024, 2025)


def _acc(y, p) -> float:
    return float(((p >= 0.5).astype(int) == y).mean())


def _fit_platt(model, cols, platt_df, raw_test):
    platt = PlattCalibrator()
    raw_p = model.predict_proba(platt_df[cols].values)[:, 1]
    platt.fit(raw_p, platt_df["home_win"].values)
    return platt.transform(raw_test)


def _blend_weight(y_val, p_model, p_elo) -> float:
    best_w, best = 1.0, -1.0
    for w in np.linspace(0.0, 1.0, 21):
        acc = _acc(y_val, w * p_model + (1.0 - w) * p_elo)
        if acc > best + 1e-9:
            best, best_w = acc, float(w)
    return best_w


def _tossup_cut(y_val, p_model, p_elo) -> float:
    best_t, best = 0.0, _acc(y_val, p_model)
    for t in (0.03, 0.05, 0.07, 0.10, 0.12):
        use_elo = np.abs(p_model - 0.5) < t
        mixed = np.where(use_elo, p_elo, p_model)
        acc = _acc(y_val, mixed)
        if acc > best + 1e-9:
            best, best_t = acc, t
    return best_t


def _gbr(train, cols):
    clf = GradientBoostingClassifier(
        n_estimators=120,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.85,
        random_state=42,
    )
    clf.fit(train[cols].values, train["home_win"].values)
    return clf


def evaluate(name: str, feat: pd.DataFrame, elo: np.ndarray, factory) -> dict:
    folds = []
    games = []
    for season in SEASONS:
        prior = feat[feat["season"] < season]
        test = feat[feat["season"] == season]
        if prior.empty or test.empty:
            continue
        prior_seasons = sorted(int(s) for s in prior["season"].unique())
        platt_season = prior_seasons[-1]
        base = prior[prior["season"] < platt_season]
        platt_df = prior[prior["season"] == platt_season]
        if base.empty:
            base, platt_df = prior, prior.iloc[0:0]
        idx = test.index.to_numpy()
        p, meta = factory(base, platt_df, test, elo[idx], prior)
        y = test["home_win"].astype(int).to_numpy()
        p = np.clip(p, 1e-6, 1 - 1e-6)
        m = compute_metrics(name, y, p)
        reg = test["is_preseason"].fillna(0).astype(int) == 0
        folds.append(
            {
                "season": season,
                "games": int(len(y)),
                "accuracy": round(m.accuracy, 4),
                "accuracy_pct": round(m.accuracy * 100, 1),
                "log_loss": round(m.log_loss, 4),
                "regular_pct": round(_acc(y[reg], p[reg]) * 100, 1) if reg.any() else None,
                **meta,
            }
        )
        for row, prob, actual in zip(test.itertuples(index=False), p, y):
            pick_home = int(prob >= 0.5)
            games.append(
                {
                    "season": int(row.season),
                    "date": str(pd.Timestamp(row.date).date()),
                    "game_id": str(row.game_id),
                    "away_team": row.away_team,
                    "home_team": row.home_team,
                    "game_type": getattr(row, "game_type", "regular"),
                    "away_pct": round((1.0 - float(prob)) * 100, 1),
                    "home_pct": round(float(prob) * 100, 1),
                    "pick": row.home_team if pick_home else row.away_team,
                    "actual": row.home_team if actual else row.away_team,
                    "correct": int(pick_home == actual),
                }
            )
    total = sum(f["games"] for f in folds)
    correct = sum(int(round(f["accuracy"] * f["games"])) for f in folds)
    return {
        "name": name,
        "folds": folds,
        "aggregate_pct": round(100 * correct / total, 1) if total else None,
        "min_season_pct": min(f["accuracy_pct"] for f in folds) if folds else None,
        "games": games,
    }


def main() -> None:
    raw = load_games()
    feat = build_features_for_history(raw).reset_index(drop=True)
    elo = predict_elo(feat)

    def v1_platt(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS
        model = train_logistic(base, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        p = _fit_platt(model, cols, platt_df, raw_t) if len(platt_df) >= 40 else raw_t
        return p, {}

    def v2_platt(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        p = _fit_platt(model, cols, platt_df, raw_t) if len(platt_df) >= 40 else raw_t
        return p, {}

    def v2_raw(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        return model.predict_proba(test[cols].values)[:, 1], {}

    def v2_regular_train(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        tr = base[base["is_preseason"].fillna(0).astype(int) == 0]
        if len(tr) < 80:
            tr = base
        model = train_logistic(tr, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        cal_src = platt_df[platt_df["is_preseason"].fillna(0).astype(int) == 0]
        if len(cal_src) >= 40:
            raw_t = _fit_platt(model, cols, cal_src, raw_t)
        return raw_t, {}

    def v2_blend(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        if len(platt_df) >= 40:
            p_model = _fit_platt(model, cols, platt_df, raw_t)
            p_val = _fit_platt(
                model, cols, platt_df, model.predict_proba(platt_df[cols].values)[:, 1]
            )
            # Don't transform val with itself — use raw val vs elo on platt season
            p_val = model.predict_proba(platt_df[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([base, platt_df]).reset_index(drop=True))[-len(platt_df) :]
            w = _blend_weight(platt_df["home_win"].astype(int).values, p_val, elo_val)
        else:
            p_model, w = raw_t, 0.7
        return w * p_model + (1.0 - w) * elo_p, {"blend_w": round(w, 2)}

    def v2_tossup_elo(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        p_model = _fit_platt(model, cols, platt_df, raw_t) if len(platt_df) >= 40 else raw_t
        if len(platt_df) >= 40:
            p_val = model.predict_proba(platt_df[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([base, platt_df]).reset_index(drop=True))[-len(platt_df) :]
            t = _tossup_cut(platt_df["home_win"].astype(int).values, p_val, elo_val)
        else:
            t = 0.05
        mixed = np.where(np.abs(p_model - 0.5) < t, elo_p, p_model)
        return mixed, {"tossup_cut": t}

    def v2_weighted(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        train = base
        w = np.where(train["is_preseason"].fillna(0).astype(int) == 1, 0.25, 1.0)
        pipe = Pipeline(
            [
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=1000, random_state=42)),
            ]
        )
        pipe.fit(train[cols].values, train["home_win"].values, clf__sample_weight=w)
        raw_t = pipe.predict_proba(test[cols].values)[:, 1]
        if len(platt_df) >= 40:
            raw_t = _fit_platt(pipe, cols, platt_df, raw_t)
        return raw_t, {}

    def v2_gbr(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        clf = _gbr(base, cols)
        return clf.predict_proba(test[cols].values)[:, 1], {}

    def v2_blend_regular(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        tr = base[base["is_preseason"].fillna(0).astype(int) == 0]
        if len(tr) < 80:
            tr = base
        model = train_logistic(tr, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        cal = platt_df[platt_df["is_preseason"].fillna(0).astype(int) == 0]
        if len(cal) >= 40:
            p_model = _fit_platt(model, cols, cal, raw_t)
            p_val = model.predict_proba(cal[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([tr, cal]).reset_index(drop=True))[-len(cal) :]
            w = _blend_weight(cal["home_win"].astype(int).values, p_val, elo_val)
        else:
            p_model, w = raw_t, 0.65
        return w * p_model + (1.0 - w) * elo_p, {"blend_w": round(w, 2)}

    def v2_adaptive(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        raw_t = model.predict_proba(test[cols].values)[:, 1]
        p_model = _fit_platt(model, cols, platt_df, raw_t) if len(platt_df) >= 40 else raw_t
        if len(platt_df) >= 40:
            p_val = model.predict_proba(platt_df[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([base, platt_df]).reset_index(drop=True))[-len(platt_df) :]
            y_val = platt_df["home_win"].astype(int).values
            w = _blend_weight(y_val, p_val, elo_val)
            t = _tossup_cut(y_val, p_val, elo_val)
            model_acc = _acc(y_val, p_val)
            elo_acc = _acc(y_val, elo_val)
            if elo_acc >= model_acc:
                w = min(w, 0.45)
                t = max(t, 0.08)
        else:
            w, t = 0.6, 0.06
        blended = w * p_model + (1.0 - w) * elo_p
        mixed = np.where(np.abs(p_model - 0.5) < t, elo_p, blended)
        return mixed, {"blend_w": round(w, 2), "tossup_cut": t}

    def v2_vote(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        log = train_logistic(base, cols)
        gbr = _gbr(base, cols)
        p_log = log.predict_proba(test[cols].values)[:, 1]
        p_gbr = gbr.predict_proba(test[cols].values)[:, 1]
        if len(platt_df) >= 40:
            p_log = _fit_platt(log, cols, platt_df, p_log)
        p = (p_log + p_gbr + elo_p) / 3.0
        return p, {}

    def v2_raw_tossup(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        model = train_logistic(base, cols)
        p_model = model.predict_proba(test[cols].values)[:, 1]
        if len(platt_df) >= 40:
            p_val = model.predict_proba(platt_df[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([base, platt_df]).reset_index(drop=True))[-len(platt_df) :]
            t = _tossup_cut(platt_df["home_win"].astype(int).values, p_val, elo_val)
        else:
            t = 0.07
        return np.where(np.abs(p_model - 0.5) < t, elo_p, p_model), {"tossup_cut": t}

    def v2_gbr_tossup(base, platt_df, test, elo_p, prior):
        cols = FEATURE_COLUMNS_V2
        clf = _gbr(base, cols)
        p_model = clf.predict_proba(test[cols].values)[:, 1]
        if len(platt_df) >= 40:
            p_val = clf.predict_proba(platt_df[cols].values)[:, 1]
            elo_val = predict_elo(pd.concat([base, platt_df]).reset_index(drop=True))[-len(platt_df) :]
            t = _tossup_cut(platt_df["home_win"].astype(int).values, p_val, elo_val)
        else:
            t = 0.07
        return np.where(np.abs(p_model - 0.5) < t, elo_p, p_model), {"tossup_cut": t}

    factories = [
        ("v1_platt", v1_platt),
        ("v2_platt", v2_platt),
        ("v2_raw", v2_raw),
        ("v2_regular_train", v2_regular_train),
        ("v2_blend_elo", v2_blend),
        ("v2_tossup_elo", v2_tossup_elo),
        ("v2_preseason_downweight", v2_weighted),
        ("v2_gbr", v2_gbr),
        ("v2_regular_blend_elo", v2_blend_regular),
        ("v2_adaptive", v2_adaptive),
        ("v2_vote", v2_vote),
        ("v2_raw_tossup", v2_raw_tossup),
        ("v2_gbr_tossup", v2_gbr_tossup),
    ]

    results = []
    best = None
    for name, fn in factories:
        row = evaluate(name, feat, elo, fn)
        slim = {k: v for k, v in row.items() if k != "games"}
        results.append(slim)
        print(
            f"{name:<24} agg={row['aggregate_pct']}%  min={row['min_season_pct']}%  "
            + "  ".join(f"{f['season']}={f['accuracy_pct']}%" for f in row["folds"])
        )
        if best is None or (
            (row["min_season_pct"] or 0) > (best["min_season_pct"] or 0)
            or (
                row["min_season_pct"] == best["min_season_pct"]
                and (row["aggregate_pct"] or 0) > (best["aggregate_pct"] or 0)
            )
        ):
            best = row

    OUT.write_text(
        json.dumps({"candidates": results, "best": best["name"] if best else None}, indent=2),
        encoding="utf-8",
    )
    print(f"\nBest by min-season then aggregate: {best['name'] if best else None}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
