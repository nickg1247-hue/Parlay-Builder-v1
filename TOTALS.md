# MLB totals model (Over/Under) v1

**Artifact:** `data/processed/mlb_totals_model.joblib`  
**Metrics:** `data/processed/mlb_totals_metrics.json`

Separate from moneyline production (`v3_logistic_pruned_platt`). Do not expect ~80% direction accuracy; realistic calibrated range is **~53–57%** on edge-flagged plays.

## Target

- **Regression:** `total_runs = home_score + away_score` (GradientBoostingRegressor)
- **P(Over):** Poisson mean = predicted total vs book `ou_line`
- **Evaluation:** MAE on runs; **log loss** on over/under vs vig-free market implied

## Production gate

Holdout **2025** games with matched free SBR O/U lines:

```
model log_loss <= market implied log_loss
```

No accuracy-percent gate.

**Latest train (2026-06-29, + park_offense_interaction / starter_rest_diff):**

| Model | MAE (runs) | O/U log loss |
|-------|------------|--------------|
| league_avg (~8.8) | 3.61 | 0.741 |
| gbr_totals + Poisson | 3.58 | 0.736 |
| market_implied | — | **0.693** |

Gate **not passed** on this run — artifact saved for dashboard/live scoring; moneyline production unchanged. Dashboard marks O/U as **experimental** (`totals_experimental`) and suppresses +EV totals badges until `production_ready: true` in the active model manifest.

## Features (`app/features/mlb_totals_pregame.py`)

Per team, using only games with `date < game_date`:

- Season / L10 / L30 runs scored & allowed per game
- Home/away split runs scored & allowed
- `park_factor_runs`, starter ERA/WHIP/IP, rest days
- `park_offense_interaction` (park × combined offense rate)
- `starter_rest_diff` (home starter rest − away starter rest)
- `h2h_avg_total_runs` (last 5 meetings, capped)

## Historical O/U lines

Same free JSON as moneylines ([SBR-derived release](https://github.com/ArnavSaraogi/mlb-odds-scraper/releases)):

```powershell
python scripts/load_mlb_totals_odds_free.py
```

→ `data/processed/mlb_totals_2025.csv` (`ou_line`, `over_odds`, `under_odds`)

## Live odds

The Odds API: **one request** with `markets=h2h,totals` ≈ **1 credit** (same as h2h-only). See `DEV.md`.

## Pick / edge

- **Pick:** OVER if `expected_total_runs > ou_line + margin`, UNDER if below (margin default **0.0**, env `TOTALS_PICK_MARGIN`)
- **Edge:** `|model P(over) − market P(over)|`; +EV flag at **8%** (`DEFAULT_MIN_EDGE`)

## Commands

```powershell
python scripts/load_mlb_totals_odds_free.py
python scripts/train_mlb_totals.py
python scripts/backtest_mlb_totals_recent.py --days 7
```

Dashboard demo: `http://127.0.0.1:8000/?date=2025-08-15&use_cache=true`
