# Local development

## Prerequisites

- Python 3.11 or newer
- PowerShell (Windows)

## One-time setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy environment template (optional; defaults work for local dev):

```powershell
Copy-Item .env.example .env
```

## Run the app

```powershell
.\scripts\dev.ps1
```

The script creates `.venv` if missing, installs dependencies, and starts uvicorn with reload.

Open in your browser:

- **App:** http://127.0.0.1:8000
- **Health:** http://127.0.0.1:8000/health

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

## MLB data ingest (Phase 1)

**Sources**

- **Game results, scores, starting pitchers:** [MLB Stats API](https://statsapi.mlb.com/) (public, no API key) via `httpx`
- **Pitcher ERA:** [pybaseball](https://github.com/jldbc/pybaseball) `pitching_stats_bref` (Baseball Reference). FIP is not on BREF tables and is left null for now.

**Seasons ingested:** 2023, 2024, 2025 (regular season, completed games only)

**Run ingest** (from project root; needs network):

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/ingest_mlb.py
```

**Expected runtime:** about 5–15 minutes on first run (thousands of boxscore calls for starting pitchers). Schedule fetch is fast; boxscores run in parallel.

**Outputs** (gitignored)

- `data/processed/mlb_games.parquet` and `mlb_games.csv`
- SQLite table `mlb_games` in `data/parlay_builder.db`

**Validate**

```powershell
python scripts/validate_mlb_data.py
```

Checks row count, date range, null counts, and duplicate `game_id` (must be 0).

Rolling features (`home_last10_*`, `away_last10_*`) use only games **before** each row (no leakage). Early-season games have null rolling stats until a team has prior games.

## MLB baseline model (Phase 2)

**Train** (requires Phase 1 data in SQLite or parquet):

```powershell
python scripts/train_mlb_baseline.py
```

**Split:** 2023–2024 train · 2025 holdout (time-based, no shuffle).

**Imputation** (applied before train/test split using train-only stats where noted):

| Field | Rule |
|-------|------|
| `home_pitcher_era` / `away_pitcher_era` | Season median ERA from 2023–2024 training games |
| `home_last10_win_pct` / `away_last10_win_pct` | `0.5` when null (opening day / no history) |
| `home_last10_run_diff` / `away_last10_run_diff` | `0.0` when null |
| `home_rest_days` / `away_rest_days` | Median rest days from 2023–2024 training games |

FIP columns are ignored (all null). See `MODEL.md` for holdout metrics and phase gate.

## MLB market comparison (Phase 3)

From project root:

```powershell
python scripts/load_mlb_odds_free.py
python scripts/evaluate_mlb_market.py
```

Optional edge threshold: `python scripts/evaluate_mlb_market.py --edge-threshold 0.03`

**Historical odds:** Free JSON release from [mlb-odds-scraper](https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset) (SportsBookReview-derived, pre-built; not live sportsbook scraping). Cached to `data/processed/mlb_odds_2025.csv`.

**Live odds stub (optional):** Sign up at [the-odds-api.com](https://the-odds-api.com) (free tier, 500 credits/month). Add to `.env`:

```
ODDS_API_KEY=your_key_here
```

One request for all MLB games ≈ **1 credit**, whether you request `markets=h2h` only or `markets=h2h,totals` together (same endpoint). `app/odds/the_odds_api.py` fetches both moneyline and O/U in a single call when `ODDS_API_KEY` is set. Skips gracefully if the key is empty. Do not use paid/historical Odds API endpoints.

See `MARKET.md` for match rate, paper-trade ROI, and advisor recommendation.

## MLB parlay ranker (Phase 4)

```powershell
python scripts/rank_mlb_parlays.py
```

Historical demo (no API key):

```powershell
python scripts/rank_mlb_parlays.py --date 2025-08-15 --use-cache
```

Defaults: 2–4 legs, cross-game only, `min_edge=5%`, top 5 parlays. See `PARLAY.md` for formulas and assumptions.

## Daily dashboard (Phase 5)

**Start server:**

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Or open browser automatically:

```powershell
.\scripts\open_daily.ps1
```

**URLs:**

| Mode | URL |
|------|-----|
| Live dashboard | http://127.0.0.1:8000 |
| Demo (cached odds) | http://127.0.0.1:8000/?date=2025-08-15&use_cache=true |
| API (live) | http://127.0.0.1:8000/api/daily |
| API (demo) | http://127.0.0.1:8000/api/daily?date=2025-08-15&use_cache=true |

Live mode requires `ODDS_API_KEY` in `.env`. The board caches responses for 5 minutes in `data/processed/daily_board.json` unless you click **Refresh** or pass `refresh=true`.

## MLB totals O/U (v1)

**Train** (requires Phase 1 games + totals odds CSV):

```powershell
python scripts/load_mlb_totals_odds_free.py
python scripts/train_mlb_totals.py
python scripts/backtest_mlb_totals_recent.py --days 7
```

See `TOTALS.md` for metrics and production gate (log loss vs market, not accuracy %).

Demo dashboard includes O/U columns when `use_cache=true` and `mlb_totals_2025.csv` is present.

Fast moneyline-only board: `http://127.0.0.1:8000/api/daily?date=2025-08-15&use_cache=true&skip_totals=true`

**Live board** (default skips totals for speed): `http://127.0.0.1:8000/` — add `?skip_totals=false` to include O/U on live odds.
