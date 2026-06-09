# NBA point spread model

## Data source

Live spreads come from The Odds API `spreads` market, fetched in the **same request** as `h2h` (`h2h,spreads` — still ~1 credit, same pattern as MLB). Median point and American price across books are stored in `nba_odds_repository/` and merged on the daily board.

**Demo / CSV mode:** Free `nba_odds_2026.csv` has moneyline only — spread columns are `null` until live odds or a repository snapshot with spreads is captured.

## Cover rule

A team covers when:

`team_score + spread_point > opponent_score`

Examples at ±5.5 (proxy holdout lines):

- Home -5.5, final 110–100 → home covers (win by 10).
- Home -5.5, final 108–105 → away +5.5 covers (loss by 3).

## Market implied probability

`market_probs_from_american_spread` converts home/away American prices with vig removal (same pattern as moneyline). Implementation: `app/odds/spread_math.py`.

## Model v1 (margin regression + Normal CDF)

**Artifacts:**

| File | Purpose |
|------|---------|
| `data/processed/nba_margin_model.joblib` | GBR + metadata |
| `data/processed/nba_margin_metrics.json` | Holdout metrics + gate |
| `data/processed/active_nba_margin_model.json` | Manifest (`production_ready`) |

| Piece | Choice |
|-------|--------|
| Features | `FEATURE_COLUMNS_WAVE2` (22 cols) |
| Target | `home_score - away_score` |
| Estimator | `GradientBoostingRegressor` (120 trees, depth 3) |
| Train | Seasons 2024 + 2025 |
| Holdout | Season 2026 |
| Residual std | Holdout margin residual std → `margin_std` (fallback 12.0) |

At inference:

- `model_prob_home_cover` = P(margin > −`home_spread_point`) via Normal CDF
- `model_prob_away_cover` = P(margin < `away_spread_point`) via Normal CDF
- `predict_home_win_proba_from_margin` = P(margin > 0)

## Training limitation

**No free historical NBA spread CSV.** Training uses final scores only. Holdout evaluation uses a **±5.5 proxy** on 2026 games (informational). Live board uses actual book lines from the API.

Train: `python scripts/train_nba_margin.py`

## Production gate (`margin_production_gate_passes`)

| Check | Threshold |
|-------|-----------|
| Holdout MAE | < 15.0 points |
| Proxy cover log loss (home & away) | < 0.693 (beats 50/50) |
| Margin-derived ML log loss | ≤ v2 logistic + 0.005 |

When gate passes → `production_ready: true` on manifest → board spread columns enabled.

**Moneyline:** Active logistic model (`v2_score_rolling`) is **not** replaced unless margin-derived P(home win) strictly beats v2 holdout log loss **and** passes `production_gate_passes()`.

## Disclaimer

Spread model is **experimental** and **not betting-ready**. No forward CLV workflow for spreads in this phase.

## Out of scope (v1)

- Totals O/U (NBA-F3)
- Spread legs in parlays
- Forward CLV for spreads
- Historical Odds API spread backtest
