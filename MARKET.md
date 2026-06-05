# MLB market comparison (Phase 3)

## Production model

| Item | Value |
|------|--------|
| **Artifact** | `data/processed/mlb_baseline_model.joblib` |
| **Version** | `v3_logistic_pruned_platt` |
| **Inference** | `predict_home_win_proba()` — base logistic (train 2023) + Platt calibration (fit 2024) |
| **Eval script** | `scripts/evaluate_mlb_market.py` loads the same artifact as the daily board |

See `MODEL.md` for holdout log loss (0.6762 on 2025) and production gate.

## Edge threshold (single default)

| Setting | Value |
|---------|--------|
| **+EV flag** | **8%** (`DEFAULT_MIN_EDGE = 0.08`) |
| **Used by** | `evaluate_mlb_market.py`, `/api/daily`, `/mlb` board (singles, parlays, totals) |
| **Override** | `python scripts/evaluate_mlb_market.py --edge-threshold 0.05` or `/api/daily?min_edge=0.05` |

One threshold everywhere avoids mixing 2% market-eval counts with 8% UI filters.

## Odds source

| Item | Detail |
|------|--------|
| **Dataset** | [mlb-odds-scraper release `dataset`](https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset) |
| **File** | `mlb_odds_dataset.json` (~76 MB) |
| **Origin** | SportsBookReview historical lines collected by community scraper (not live sportsbook scraping by this project) |
| **Books** | Median `currentLine` across BetMGM, FanDuel, Caesars, Bet365, DraftKings, BetRivers |
| **License** | Follow [repository license](https://github.com/ArnavSaraogi/mlb-odds-scraper) (MIT); MLB/SBR data subject to their terms |

**Line type note:** We use SBR `currentLine` median as a **closing proxy**, not verified official closing lines. Opening lines are also in the JSON. This limits CLV claims until true closing or The Odds API historical (paid) data is added.

**Data hygiene:** American odds must satisfy `100 ≤ |odds| ≤ 500` to drop scraper placeholders (e.g. `-1`, `-2`).

## 2025 holdout match rate

| Metric | Value |
|--------|-------|
| Holdout games | 2,430 |
| Matched with odds | 1,758 |
| **Match rate** | **72.35%** |

Unmatched games: missing from SBR export, invalid odds, or team/date alignment gaps.

## Summary metrics (edge threshold 8%)

Regenerate after ingest + train + odds load:

```powershell
python scripts/load_mlb_odds_free.py
python scripts/evaluate_mlb_market.py
```

Outputs (gitignored): `data/processed/mlb_odds_2025.csv`, `data/processed/mlb_2025_market_eval.csv`, `data/processed/mlb_market_metrics.json`

| Metric | Value (re-run to refresh) |
|--------|---------------------------|
| Log loss — model (`v3_logistic_pruned_platt`) | 0.6762 (holdout, from `MODEL.md`) |
| Log loss — market (vig-free) | 0.6770 |
| Model beats market (log loss) | Yes (slightly) |
| +EV picks flagged (≥8%) | *run eval* |
| Paper-trade ROI (flat $1) | *run eval* |
| +EV hit rate | *run eval* |
| EV signal (ROI > 0) | *run eval* |

Prior 2% threshold runs flagged ~80% of matched games — too loose for production. The 8% filter aligns with the daily board and backtest report.

## Advisor recommendation (Phase 5 exit)

**Proceed with daily board workflow; treat +EV counts as experimental.**

- Production calibration **beats v1 and matches/beats market log loss** on 2025 holdout with Platt (`v3_logistic_pruned_platt`).
- Free SBR medians are not confirmed closings; forward CLV logging (Odds API live key) is required before Phase 6.
- Use **8% edge** as the single production filter until advisor reviews forward CLV.
- Phase 4 parlay ranker is infrastructure only — validate lines before any real wager.

## Scripts

```powershell
python scripts/load_mlb_odds_free.py
python scripts/evaluate_mlb_market.py
```
