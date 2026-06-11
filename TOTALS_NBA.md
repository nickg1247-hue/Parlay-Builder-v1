# NBA over/under (total points) model

## Data source

Live O/U lines come from The Odds API `totals` market, fetched in the **same request** as `h2h` and `spreads` (`h2h,spreads,totals` — still ~1 credit). Median line and over/under American prices are stored in `nba_odds_repository/` and merged on the daily board.

**Demo / CSV mode:** Optional columns on `nba_odds_2026.csv`: `ou_line`, `over_odds`, `under_odds`. Moneyline-only CSV still works; O/U columns are null until live odds or CSV import includes them.

## Over rule

- Half-point line (e.g. 224.5): over wins when `home_score + away_score > line`.
- Whole-number line: over wins when combined score `>= line` (push possible).

Implementation: `actual_went_over()` in `app/models/nba_totals.py`.

## Over probability: Normal CDF (not Poisson)

| Choice | Rationale |
|--------|-----------|
| **Normal CDF** (selected) | NBA combined totals ~220 pts with moderate variance (~18 std). Continuous Normal fits the regression target (expected total) and half-point lines cleanly. |
| Poisson (not used) | Better for low-count targets (MLB runs ~9). Combined NBA scores are too high-mean for Poisson without extra tuning. |

`prob_over_normal(expected_total, std, ou_line)` uses holdout residual std from the GBR model (`app/odds/spread_math.norm_cdf`).

## Model v1 (GBR + Normal)

**Artifacts:**

| File | Purpose |
|------|---------|
| `data/processed/nba_totals_model.joblib` | GBR regressor + metadata |
| `data/processed/nba_totals_metrics.json` | Holdout metrics + gate |
| `data/processed/active_nba_totals_model.json` | Manifest (`production_ready`) |

| Piece | Choice |
|-------|--------|
| Features | `FEATURE_COLUMNS_WAVE2` via `app/features/nba_totals_pregame.py` |
| Target | `home_score + away_score` |
| Estimator | `GradientBoostingRegressor` (120 trees, depth 3) |
| Train | Seasons 2024 + 2025 |
| Holdout | Season 2026 |

**Train:**

```powershell
python scripts/train_nba_totals.py
```

## Production gate (separate from moneyline)

Promote to board (`board_totals_enabled=true`) only when **all** hold:

1. Holdout games with O/U lines exist in `nba_odds_2026.csv` (or captured repository).
2. Model O/U log loss ≤ market log loss (de-vigged over prob from book prices).
3. Model MAE on total points ≤ league-average baseline MAE (constant 220.0).

Gate function: `totals_production_gate_passes()` in `app/models/nba_totals.py`.

Moneyline `betting_ready` and spread `board_spread_enabled` are independent — a totals model can fail the gate while ML/spread remain on the board.

## UI surfaces

| Surface | When O/U shows |
|---------|----------------|
| `/nba/board` | `board_totals_enabled` — optional O/U columns + top totals section |
| `/nba/game/{id}` | Same gate; game insights always loads totals model when gate passes (`skip_totals=false`) |
| `/api/nba/daily?skip_totals=false` | Force totals columns in live/demo board |

Default demo board skips totals (`skip_totals=true` when `use_cache=true`) to mirror MLB `skip_totals` pattern for faster demo loads without O/U CSV columns.
