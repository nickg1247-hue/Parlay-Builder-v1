# CFB moneyline model (Phase C2a)

## v1 baseline

| Item | Value |
|------|-------|
| Target | `home_win` (moneyline) |
| Train | Logistic regression on **2022–2023** |
| Calibration | Platt sigmoid fit on **2024** |
| Holdout | **2025** regular season |
| Features | Elo diff, season win %, rest days, home field, B2B flags |
| Artifact | `data/processed/cfb_baseline_model.joblib` |
| Metrics | `data/processed/cfb_baseline_metrics.json` |
| Manifest | `data/processed/active_cfb_model.json` |

## Production gate

Holdout **log loss** must beat the best naive baseline (constant home-win rate or simple Elo). No market odds in v1 — gate compares model vs naives only.

```text
passes = model_log_loss < min(naive_home_rate_log_loss, elo_log_loss)
```

## Features (`cfb_v1`)

- `elo_diff` — pre-game Elo home minus away (K=20, home adv=55)
- `home_season_win_pct` / `away_season_win_pct` — in-season record before kickoff
- `home_rest_days` / `away_rest_days` — days since last game (median imputation)
- `rest_diff` — home rest minus away rest
- `home_field` — always 1 (home perspective)
- `home_b2b` / `away_b2b` — played previous calendar day

## Inference

`predict_home_win_proba()` loads the artifact, builds slate features from CFBD history, applies logistic + Platt.

Slate API: `GET /api/cfb/predictions?date=YYYY-MM-DD`

## Odds sport key (Phase 3+)

Document only — not wired in Phase 1:

`americanfootball_ncaaf`

## Train / bootstrap

```powershell
python scripts/bootstrap_cfb.py
# or
python scripts/train_cfb_baseline.py
```

Requires `data/processed/cfb_games.parquet` from ingest (`CFBD_API_KEY`).

## Spread & totals (Phase 2)

| Track | Artifact | Docs |
|-------|----------|------|
| Spread | `cfb_margin_model.joblib` | `SPREAD_CFB.md` |
| Totals | `cfb_totals_model.joblib` | `TOTALS_CFB.md` |

Train spread/totals:

```powershell
python scripts/train_cfb_margin.py
python scripts/train_cfb_totals.py
```

Slate API adds `spread_pick`, `expected_total_pts`, `totals_pick` on `/api/cfb/predictions` (proxy lines until Phase 3 odds).

## Walk-forward backtest (proof on saved seasons)

Expanding-window test: for each holdout season, train on **all prior seasons only**, predict every game, compare to actual results. Features are built chronologically (no same-day leakage).

```powershell
python scripts/backtest_cfb_seasons.py
# or API: GET /api/cfb/backtest?refresh=true
```

Output: `data/processed/cfb_backtest_report.json`

| Report section | Meaning |
|----------------|---------|
| `folds[]` | Per-season holdout: ML accuracy, log loss, spread/O/U pick accuracy |
| `aggregate` | Weighted metrics across all holdout games |
| `feature_effects.logistic_importance_avg` | Which inputs moved the moneyline model most |
| `proof_summary.verdict` | `passes_walk_forward` if ML beats naive baseline every fold |

**Current data:** 4 seasons ingested (2022–2025). Re-run `python scripts/bootstrap_cfb.py` to pull **2021** for a 5-season backtest.

Spread/totals folds use proxy lines (-7, train-median O/U) — not sportsbook closes. Market proof requires Phase 3 odds.
