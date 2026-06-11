# CFB over/under (total points) model

## Data source

Phase 2 slate uses **per-game O/U lines** from CFBD betting lines (median across providers). When CFBD is unavailable, a **matchup proxy** from season scoring averages is used instead of a flat league line.

Live O/U (Phase 3) can additionally use Odds API `totals` on `americanfootball_ncaaf`.

## Over rule

Half-point line: over wins when `home_score + away_score > line`.  
Whole-number line: over wins when combined score `>= line` (push possible).

Implementation: `actual_went_over()` in `app/models/cfb_totals.py`.

## Over probability: Normal CDF

Same approach as NBA — combined CFB totals ~50–55 pts with ~14 pt residual std. `prob_over_normal()` uses `norm_cdf` from `app/odds/spread_math.py`.

## Model v1 (GBR + Normal)

| File | Purpose |
|------|---------|
| `data/processed/cfb_totals_model.joblib` | GBR regressor + metadata |
| `data/processed/cfb_totals_metrics.json` | Holdout metrics + gate |
| `data/processed/active_cfb_totals_model.json` | Manifest (`production_ready`) |

| Piece | Choice |
|-------|--------|
| Features | `TOTALS_FEATURE_COLUMNS` (same scoring cols as margin) |
| Target | `home_score + away_score` |
| Estimator | `GradientBoostingRegressor` (120 trees, depth 3) |
| Train | Seasons **2022–2024** |
| Holdout | **2025** |

## Proxy O/U (holdout gate)

Proxy line = train median total, rounded to nearest half-point (e.g. 52.5).  
Gate: model over log loss **≤** league-constant log loss at proxy line, and model MAE **≤** league-average MAE.

## Train

```powershell
python scripts/train_cfb_totals.py
# or full stack:
python scripts/bootstrap_cfb.py
```

## Slate API fields

`GET /api/cfb/predictions?date=` adds:

- `expected_total_pts`, `model_prob_over`, `ou_line`, `ou_line_source`, `totals_pick`, `totals_confidence`
