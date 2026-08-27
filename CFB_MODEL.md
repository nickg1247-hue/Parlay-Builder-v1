# CFB moneyline model

## Active production model: **v4** (`cfb_v4`) when it beats v3

College football is not an NFL clone. v4 blends **offseason priors** (returning production, talent, last-year FPI, preseason SP+, coach change) with **in-season** Elo + rolling SRS. Priors dominate Weeks 1–3 and fade to 30% by Week 8 — they never turn off.

| Item | Value |
|------|-------|
| Target | `home_win` (moneyline) |
| Train | Logistic regression on **2022–2023** |
| Calibration | Platt sigmoid fit on **2024** |
| Holdout | **2025** regular season |
| **Active** | **v4_logistic_platt** if holdout log loss beats v3 + naive; else stay on v3 |
| Artifact | `data/processed/cfb_baseline_model.joblib` |
| Metrics | `data/processed/cfb_baseline_metrics.json` |
| Manifest | `data/processed/active_cfb_model.json` |
| Confidence | `data/processed/cfb_confidence_cuts.json` — toss-up / soft / hard / lock fit on CFB walk-forward, not NFL cuts |

### Holdout comparison (2025)

| Model | Log loss | Accuracy |
|-------|----------|----------|
| v1 (Elo + rest) | 0.555 | 72.4% |
| v2 (+ form/conf) | 0.547 | 73.1% |
| v3 (+ SP+ all year) | 0.427 | 82.3% |
| **v4 (priors + SRS + week blend)** | **0.414** | **82.0%** |

Promotion rule: highest tier that beats the prior tier on holdout log loss **and** beats naive Elo/home-rate baseline. Market eval is advisory (does not block promotion).

### v4 features (college-native)

- v3 columns, plus talent / returning PPA / returning passing PPA / prior-year FPI / coach-change flags
- Rolling opponent-adjusted SRS (from our results, not CFBD weekly SP+)
- P4 / G5 / FCS matchup tier, conference game, program home margin
- `prior_weight` and `blended_quality_diff` (Week 1–3 ≈ 70% prior, Week 8+ ≈ 30% prior)
- When CFBD weekly SP+ is flat, the preseason snapshot is used for **Week 1 only**; Week 2+ SP+ inputs stay neutral until a real prior-week snapshot exists.

Priors cache (season-level CFBD only): `python scripts/fetch_cfb_priors.py` → `data/processed/cfb_priors_cache/`. Current-year FPI is stored but **not** used as a feature (leakage).

## v1 baseline (legacy)

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

## Odds sport key

`americanfootball_ncaaf` — live repository + CFBD lines. Board includes cross-game parlays. Futures: `/cfb/futures` (Sunday conference 1-through-last + 12-team playoff). Playoff uses **2026 CFP rules** (Power 4 champs + top G6 + 7 at-large). Backtest vs 2024–2025 official fields: **11/12 at selection day**, 75%+ from week 12; week 3 is still ~50% because of mid-season risers. `python scripts/backtest_cfb_playoff.py`. Preseason conference **title winners** use a prior blend (SP+ / prior FPI / talent / Elo) plus two tie-breaks (SP+ ≥ 28 outlier, close-race returning production); target ≥50% on 2024–2025 (`python scripts/backtest_cfb_conference.py`). Morning refresh: `python scripts/morning_refresh.py --sports mlb,cfb`.

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

**Current v4 walk-forward (2023–2025, 2,629 games):** 78.4% accuracy, log loss 0.454, **beats naive every fold** (2024 now passes). Confidence: toss-up 50–55%, soft 55–62.5%, hard 62.5–88.5% (75.2%), lock 88.5%+ (95.5%).

Spread/totals folds use proxy lines (-7, train-median O/U) — not sportsbook closes. Market proof: see **`MARKET_CFB.md`**.
