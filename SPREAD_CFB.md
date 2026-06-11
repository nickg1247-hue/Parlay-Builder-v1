# CFB point spread model

## Data source

Phase 2 uses **model-only** proxy lines on the slate. Live spreads (Phase 3) will come from The Odds API `spreads` market on `americanfootball_ncaaf` — same request as `h2h` and `totals`.

## Cover rule

A team covers when `team_score + spread_point > opponent_score` (see `app/odds/spread_math.py`).

## Model v1 (margin regression + Normal CDF)

| File | Purpose |
|------|---------|
| `data/processed/cfb_margin_model.joblib` | GBR regressor + metadata |
| `data/processed/cfb_margin_metrics.json` | Holdout metrics + gate |
| `data/processed/active_cfb_margin_model.json` | Manifest (`production_ready`) |

| Piece | Choice |
|-------|--------|
| Features | `MARGIN_FEATURE_COLUMNS` in `app/features/cfb_pregame.py` |
| Target | `home_score - away_score` |
| Estimator | `GradientBoostingRegressor` (120 trees, depth 3) |
| Train | Seasons **2022–2024** |
| Holdout | **2025** |
| Cover prob | Normal CDF on margin residual std |

## Proxy spread (holdout gate)

No free historical CFB spread CSV. Holdout eval uses:

- Home **-7.0** / away **+7.0**

## Production gate

Spread board enabled when:

1. Holdout MAE (margin) **< 18** pts  
2. Proxy cover log loss (home and away) **< coin flip** (0.693)

Margin-derived ML log loss is compared to moneyline baseline for sanity only — **does not replace** the moneyline artifact.

## Train

```powershell
python scripts/train_cfb_margin.py
# or full stack:
python scripts/bootstrap_cfb.py
```

## Slate API fields

`GET /api/cfb/predictions?date=` adds per game:

- `model_margin`, `model_prob_home_cover`, `spread_pick`, `spread_confidence`
- Uses proxy -7 until book lines are wired in Phase 3
