# MLB baseline model (Phase 2 / 2.5)

**Production artifact:** `data/processed/mlb_baseline_model.joblib` (v1 logistic or v2 GBC+Elo — whichever beats market on 2025 holdout log loss).

**Metrics:** `data/processed/mlb_baseline_metrics.json` (regenerate with `python scripts/train_mlb_baseline.py`).

## Holdout metrics (2025)

Run `python scripts/train_mlb_baseline.py` for latest numbers. Reference (v1 logistic before 2.5):

| Model | Log loss | Brier | Accuracy |
|-------|----------|-------|----------|
| market_implied | ~0.677 | — | — |
| logistic_regression_v1 | 0.6777 | 0.2408 | 0.577 |
| gradient_boosting_v2_elo | *(see metrics JSON)* | | |

## Phase gate (2.5)

- **Replace production model** only if v2 log loss **< market implied** on 2025 matched odds.
- If v2 does not beat market, artifact stays **v1 logistic**.

## Live scoring fix (2.5)

`build_slate_dataframe()` uses **per-starter ERA** from `app/data/pitcher_lookup.py` (ingested `mlb_games` + pybaseball BREF cache). Season median is fallback only when starter is unknown.

## UI display blend (not the training target)

```
display_prob_home = 0.5 × model_prob_home + 0.5 × market_prob_home
```

Used only in the **Win chances (simple)** section when odds exist. Training and +EV edges still use raw model probabilities.

## Edge thresholds

- Singles and parlays: **8%** minimum edge/EV (`DEFAULT_MIN_EDGE = 0.08`).

## Calibration diagnostics

- **Favorite pick agreement:** when market `P(home) > 55%`, how often does the model also favor home (`P > 0.5`)? Reported in metrics JSON after train.

## Imputation (training)

| Field | Rule |
|-------|------|
| ERA | From game data; median impute when missing in train |
| L10 win % | 0.5 when missing |
| L10 run diff | 0.0 when missing |
| Rest days | Train median |
| Elo (v2 only) | Pre-game Elo from chronological updates |

FIP is not used in the model (mostly null in BREF ingest).
