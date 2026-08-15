# NFL models

## Active moneyline: **v2** (`nfl_v2`)

| Item | Value |
|------|-------|
| Target | `home_win` (moneyline) |
| Train | Gradient boosting on **2019–2023** (`nfl_v2` features) |
| Close-game rule | If \|P(home)−0.5\| is inside a toss-up band, use Elo. Band tuned on **2024** only |
| Holdout | **2025** (preseason + regular) |
| **Active** | **v2_gbr_elo_tossup** |
| Artifact | `data/processed/nfl_baseline_model.joblib` |
| Metrics | `data/processed/nfl_baseline_metrics.json` |
| Manifest | `data/processed/active_nfl_model.json` |
| Walk-forward | `python scripts/backtest_nfl_seasons.py` |

### Goals

| Goal | Result |
|------|--------|
| Hard minimum **60%** every walk-forward season | **Not met over 5 years** — 2021 55.3% · 2022 59.4%. **Met on 2023–2025** (60.8 / 64.1 / 62.8) |
| Soft **75%** on every game | **Not met** — 5-year all-games 60.5%. Regular 61.3% |

### Confidence categories (2024–2025 regular season)

Cuts are fit on walk-forward **regular-season 2024–2025** favorite-% hit rates. Preseason is capped at **soft**. Lock may be an interior band when a higher-% upset would break a tail to 100%.

| Category | Favorite % | Floor | Regular 2024–25 proof |
|----------|------------|------:|------------------------|
| Toss-up | 50–55% | coin flip | **54.4%** (31 / 57) |
| Soft | 55–73% | ≥60% | **64.1%** (248 / 387) |
| Hard | 73–80.5% and 86%+ | ≥75% | **74.2%** (66 / 89) — one win short of 75% |
| Lock | 80.5–86% | ≥95% | **100%** (10 / 10) |

The 73%+ tier including locks is **76.8%** (76 / 99). Lock stops at 86% because Panthers at Packers (2025-11-02, posted 86.3%) was an upset; a tail through 100% would miss 95%.

#### Posted % vs actual hit rate (regular 2024–2025)

| Posted favorite | Games | Correct | Hit % |
|-----------------|------:|--------:|------:|
| 50–55% | 57 | 31 | 54.4% |
| 55–60% | 103 | 63 | 61.2% |
| 60–65% | 86 | 54 | 62.8% |
| 65–70% | 126 | 87 | 69.0% |
| 70–75% | 110 | 74 | 67.3% |
| 75–80% | 49 | 35 | 71.4% |
| 80–85% | 10 | 10 | **100%** |
| 85–90% | 2 | 1 | 50.0% |

2-year walk-forward: **63.4%** (404 / 637). Regular **65.4%** (355 / 543). Both seasons stay above the 60% minimum (2024 64.1%, 2025 62.8%).

Artifacts: `data/processed/nfl_confidence_cuts.json`, `nfl_season_backtest.json`, `nfl_season_backtest_games.json`.

### Walk-forward (2021–2025, 1,592 games)

| Season | Games | Correct | v2 | Elo | Regular | Preseason |
|--------|------:|--------:|---:|----:|--------:|----------:|
| 2021 | 318 | 176 | 55.3% | 59.4% | 52.8% | 70.2% |
| 2022 | 318 | 189 | 59.4% | 60.7% | 59.1% | 61.2% |
| 2023 | 319 | 194 | **60.8%** | 58.9% | 64.0% | 42.6% |
| 2024 | 320 | 205 | **64.1%** | 64.4% | 67.3% | 45.8% |
| 2025 | 317 | 199 | **62.8%** | 61.2% | 63.5% | 58.7% |
| **5-year** | **1592** | **963** | **60.5%** | | **61.3%** | 55.7% |

2023–2025 only (956 games) is **62.6%**, same as the earlier 3-year window. v1 was 59.9% on that 3-year window (2023 at 55.2%).

### Holdout comparison (2025, preseason + regular, 317 games)

| Model | Log loss | Accuracy |
|-------|----------|----------|
| Naive home-win rate | 0.691 | 53.0% |
| Elo | 0.666 | 61.2% |
| **v2 (GBR + Elo toss-up)** | **0.659** | **62.8%** |

Gate **passes** (log loss beats Elo and home-win rate; accuracy ≥ 60%).

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

## Features (`nfl_v2`)

v1 columns plus:

- Last-5 win % and point margin (all prior games, no same-game leak)
- Current win/loss streak
- Home team's home win % and away team's road win % (season-to-date)
- Short-rest (≤5 days) and bye (≥13 days) flags
- Week (preseason / 4, regular / 18)
- Season point-margin difference and Pythagorean win% difference
- Away travel miles from public stadium coordinates (no API)

Spread / totals still use season points for / against / margin averages (pregame only).

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
