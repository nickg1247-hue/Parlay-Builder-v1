# NFL models

## Active moneyline: **v1** (`nfl_v1`)

| Item | Value |
|------|-------|
| Target | `home_win` (moneyline) |
| Train | Logistic regression on **2019–2023** |
| Calibration | Platt sigmoid fit on **2024** |
| Holdout | **2025** (preseason + regular) |
| **Active** | **v1_logistic_platt** — Elo, form, rest, home field, divisional, preseason flag |
| Artifact | `data/processed/nfl_baseline_model.joblib` |
| Metrics | `data/processed/nfl_baseline_metrics.json` |
| Manifest | `data/processed/active_nfl_model.json` |

### Holdout comparison (2025, preseason + regular, 317 games)

| Model | Log loss | Accuracy |
|-------|----------|----------|
| Naive home-win rate | 0.691 | 53.0% |
| Elo | 0.666 | 61.2% |
| **v1 (logistic + Platt)** | **0.651** | **62.1%** |

Gate **passes** (v1 log loss beats Elo and home-win rate).

## Spread (N2)

GBR predicted home margin + Normal CDF cover probs. Proxy line **-3 / +3** when no book spread is attached.

| Item | Value |
|------|-------|
| Artifact | `data/processed/nfl_margin_model.joblib` |
| Metrics | `data/processed/nfl_margin_metrics.json` |
| Train / holdout | 2019–2024 / 2025 |
| Gate | Holdout MAE &lt; 14 and proxy cover log loss &lt; ln(2) |
| Latest holdout | MAE **10.62**, home cover log loss **0.619** — gate **passes** |

## Totals (N2)

GBR expected total points + Normal over probability. Proxy O/U is the train-set median total (half-point).

| Item | Value |
|------|-------|
| Artifact | `data/processed/nfl_totals_model.joblib` |
| Metrics | `data/processed/nfl_totals_metrics.json` |
| Train / holdout | 2019–2024 / 2025 |
| Gate | Model log loss and MAE beat a league-average baseline |
| Latest holdout | MAE **10.56** vs league 10.59; log loss **0.673** vs 0.693 — gate **passes** |

## Features (`nfl_v1`)

- `elo_diff` — pre-game Elo home minus away (K=20, home adv=55; no home adv on neutral sites)
- `home_season_win_pct` / `away_season_win_pct` — in-season record before kickoff
- `home_rest_days` / `away_rest_days` / `rest_diff` — days since last game (median imputation)
- `home_field` — 1 unless ESPN `neutralSite`
- `divisional` — same NFL division (franchise aliases: OAK→LV, WAS→WSH)
- `is_preseason` — 1 for ESPN `seasontype=1`

Spread / totals also use season points for / against / margin averages (pregame only).

No same-game leakage: Elo, win%, and scoring averages use only games strictly before kickoff.

## Data source

ESPN is the only games API. Same `game_id` in parquet, predictions, board, and `/nfl` cards.

| Purpose | Endpoint |
|---------|----------|
| Live / day slate | `site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD` |
| Historical ingest | same scoreboard with `dates={season}&seasontype={1\|2}&week={n}` |

**Included:** preseason (`seasontype=1`, weeks 1–4) and regular season (`seasontype=2`, weeks 1–18), 2019–2025.  
**Excluded:** playoffs (`seasontype=3+`) and ties.

## Odds (N3)

Live lines: The Odds API `americanfootball_nfl` plus `americanfootball_nfl_preseason`. Snapshots in `data/processed/nfl_odds_repository/`. Fallback: ESPN BET lines captured on the scoreboard ingest. See `MARKET_NFL.md`.

## Train / bootstrap

```powershell
python scripts/bootstrap_nfl.py
# or, after ingest exists:
python scripts/train_nfl_baseline.py
python scripts/train_nfl_margin.py
python scripts/train_nfl_totals.py
python scripts/evaluate_nfl_market.py
```

Writes `data/processed/nfl_games.parquet` and trains moneyline, spread, and totals artifacts.
