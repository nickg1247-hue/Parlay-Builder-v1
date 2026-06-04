# MLB baseline model (Phase 2 / 2.5 / 2.6 / 2.7)

**Production artifact:** `data/processed/mlb_baseline_model.joblib`

**Metrics:** `data/processed/mlb_baseline_metrics.json` — regenerate with `python scripts/train_mlb_baseline.py`

**Ablation (2.7):** `data/processed/mlb_ablation_results.json` — regenerate with `python scripts/ablate_mlb_features.py`

## Production (Phase 2.7)

| Item | Value |
|------|--------|
| Version | `v3_logistic_pruned_platt` |
| Features | 23 Wave 1 columns after redundancy drop (ranks removed) |
| Calibration | Platt sigmoid: base logistic trained on **2023**, Platt fit on **2024**, evaluated on **2025** |
| 2025 holdout log loss | **0.6762** (raw pruned logistic: 0.6792) |

## Benchmarks (2025 holdout)

| Model | Log loss | Notes |
|-------|----------|--------|
| market_implied | 0.6770 | Matched SBR odds |
| logistic_regression_v1 | 0.6777 | Previous production |
| logistic_wave1_pruned_platt | **0.6762** | **Current production** |
| logistic_wave1_pruned | 0.6792 | No Platt |
| logistic_regression_v2_wave1 | ~0.681 | Full Wave 1 (25 features) |

## Production gate (unchanged intent, 2.7 wording)

Replace artifact only when holdout log loss:

1. **Strictly below v1** (0.6777), and  
2. **At or below market** (0.6770).

Phase 2.7 result: **Platt + pruned Wave 1** passes; production updated from v1.

## Feature ablation (2.7)

Run `python scripts/ablate_mlb_features.py` for the full subset table. Highlights on 2025:

| Subset | Features | Log loss | Beats v1 | ≤ market |
|--------|----------|----------|----------|----------|
| v1_baseline | 8 | 0.6777 | — | no |
| v1_plus_team_last30 | 12 | 0.6776 | yes | no |
| v1_plus_park | 9 | 0.6776 | yes | no |
| wave1_pruned | 23 | 0.6792 | no | no |
| wave1_pruned + Platt | 23 | **0.6762** | yes | yes |

**Redundancy drop (Spearman \|r\| > 0.9 on 2023–24 train):** `home_win_pct_rank`, `away_win_pct_rank` (correlated ~0.97 with season win %).

## Platt calibration

```
raw_p = logistic_2023.predict(X)
calibrated_p = Platt(raw_p)   # Platt fit on 2024 only
```

Inference: `predict_home_win_proba()` applies Platt when the artifact includes `platt_calibrator`.

## UI display blend (not the training target)

```
display_prob_home = w × model_prob_home + (1 − w) × market_prob_home
```

`w` = `DISPLAY_BLEND_MODEL_WEIGHT` (default **0.5**; set **0.7** in `.env` for 70/30 model/market). Used only in **Win chances (simple)** when odds exist. Training and +EV edges use calibrated model probabilities.

## Live scoring

`build_slate_dataframe()` → `build_features_for_slate()` → `predict_home_win_proba()` (same path as training features). Starter ERA/WHIP/IP from `app/data/pitcher_lookup.py`.

## Edge thresholds

Singles and parlays: **8%** minimum edge/EV (`DEFAULT_MIN_EDGE = 0.08`).

## Imputation

| Field | Rule |
|-------|------|
| ERA / WHIP / IP | Lookup + season median / defaults |
| L10 / L30 / season rolling | Chronological; day-of-game excluded |
| Rest days | Train median |
| Park factor | Static `app/data/park_factors.csv` |
