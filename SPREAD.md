# MLB run line (spread) model

## Data source

Live run lines come from The Odds API `spreads` market, fetched in the same request as `h2h` and `totals` (`MARKETS_H2H_TOTALS_SPREADS` — still ~1 credit). Median point and American price across books are stored in the odds repository and merged on the daily board via `games_to_ml_dataframe` / `attach_market_odds`.

**Demo / historical CSV mode:** Free `mlb_odds_2025.csv` has moneyline only — spread columns are `null` on the board until live odds or a seeded repository snapshot with spreads is used.

## Cover rule

A team covers when:

`team_score + spread_point > opponent_score`

Examples at ±1.5:

- Home -1.5, final 5–3 → home covers (win by 2+).
- Home -1.5, final 5–4 → away +1.5 covers (loss by exactly 1).

## Market implied probability

`market_probs_from_american_spread` converts home/away American prices with `american_to_implied_prob` + `remove_vig` (same pattern as moneyline).

## Model v1 (margin regression + Normal CDF)

**Artifact:** `data/processed/mlb_spread_model.joblib`

| Piece | Choice |
|-------|--------|
| Features | Wave 1 pregame (`FEATURE_COLUMNS_WAVE1`) — same as early moneyline / totals baseline |
| Target | `home_score - away_score` (run margin) |
| Estimator | `GradientBoostingRegressor` (120 trees, depth 3) |
| Train | 2023–2024 seasons |
| Holdout | 2025 season |
| Residual std | Std of holdout margin residuals → `margin_std` in artifact |

At inference, for each slate row’s book line:

- `model_prob_home_cover` = P(margin > −`home_spread_point`) via Normal CDF
- `model_prob_away_cover` = P(margin < `away_spread_point`) via Normal CDF

Integer-run ties: at -1.5 the cutoff is margin > 1.5, so “win by 2+” for home favorites.

## Training limitation

No free historical run-line CSV (SBR release is ML-only). Training uses final scores only. Holdout evaluation uses a **±1.5 proxy** on 2025 games. Live board uses actual book lines from the API.

Train: `python scripts/train_mlb_spread.py`

## Daily board fields

Per slate row (when spread odds present):

- `home_spread_point`, `home_spread_american`, `away_spread_point`, `away_spread_american`
- `model_prob_home_cover`, `model_prob_away_cover`
- `market_prob_home_cover`, `market_prob_away_cover`
- `spread_edge_home`, `spread_edge_away`
- `spread_best_pick` (side / team / edge / american / spread_point) when edge ≥ `min_edge` (default 8%)
- `plus_ev_spread` (bool)

**Disclaimer:** Run line model is experimental; not validated like moneyline v3.

## Out of scope (v1)

- Spread legs in parlay ranker
- Forward CLV for spreads
- NBA / point spreads
- Paid historical spread backtest
