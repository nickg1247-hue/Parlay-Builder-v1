# Local development

## News headlines (Phase D)

Home page (`/`) shows up to **10** sports headlines from ESPN RSS. Links open ESPN in a new tab — no in-app articles.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/news` | Headlines JSON (15 min disk cache) |
| `GET /api/news?refresh=true` | Bypass cache, refetch feeds |

**Feeds:** ESPN Top + ESPN MLB (`app/services/news_feed.py`).

**Cache:** `data/processed/news_cache.json` — TTL **900s**. On feed failure, serves stale cache when available.

**Response shape:**

```json
{
  "cached_at": "2026-06-06T14:00:00+00:00",
  "cache_ttl_seconds": 900,
  "items": [{ "title", "link", "published", "source", "summary" }],
  "count": 10,
  "cache_hit": false
}
```

**Manual refresh:** `curl http://127.0.0.1:8000/api/news?refresh=true`

---

## Free mode (default) — no Odds API credits

By default the site uses **free data only**:

| Data | Source |
|------|--------|
| Schedule, scores, logos | [MLB Stats API](https://statsapi.mlb.com/) |
| Model picks, est. runs, parlays logic | Your models |
| Sportsbook ML / O/U / spread | **Off** unless you opt in |

**.env:**

```env
USE_LIVE_ODDS=false
```

Leave `USE_LIVE_ODDS=false` (or unset) even if `ODDS_API_KEY` is in `.env` — **no sportsbook API calls**.

**Historical demo** (real past lines, no API): `/mlb/board` → **Demo** or `?use_cache=true&date=2025-08-15`. Demo CSV / cache has **moneyline only** — run line columns stay empty until **Run live** (`USE_LIVE_ODDS=true` + `ODDS_API_KEY`) or a repository snapshot with `spreads` is seeded. See `SPREAD.md`.

---

## Enable live odds (checklist)

All API calls go through `fetch_from_api_if_allowed()` in `app/odds/odds_repository.py` with **20/hour** and **500/day UTC** caps. Game pages never call the API directly.

### 1. Edit `.env` manually

```env
USE_LIVE_ODDS=true
ODDS_API_KEY=your_key_here
ODDS_HOURLY_REFRESH=true
ODDS_API_MAX_PER_HOUR=20
ODDS_API_MAX_PER_DAY=500
```

Do not commit `.env`. The API key is never logged by the app.

### 2. Run the enable script

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\enable_live_odds.ps1
```

This verifies `.env`, runs `morning_refresh.py` once (quota-gated), and prints verification URLs. It does **not** modify `.env`.

### 3. Start the dev server

```powershell
.\scripts\dev.ps1
```

With `ODDS_HOURLY_REFRESH=true`, the server refreshes today's repository every **3600s** (still quota-gated).

### 4. Verify

| Check | URL / file |
|-------|------------|
| Today's lines + quota | `GET http://127.0.0.1:8000/api/odds/today` |
| Morning refresh status | `GET http://127.0.0.1:8000/api/status/refresh` |
| Game market boxes | `/mlb` → click a game → ML / O/U in team columns |
| Quota counters | `data/processed/odds_repository/quota.json` |
| Today's snapshot | `data/processed/odds_repository/YYYY-MM-DD.json` |

After `morning_refresh`, `market_cards.source` should be `the_odds_api` (or `repository` if reading cached file). `quota.json` `hour_count` / `day_count` increment **only** on successful HTTP.

### 5. Optional scheduled jobs

| Job | Script |
|-----|--------|
| Daily board + odds (morning) | `scripts/morning_refresh.ps1` |
| Hourly line refresh | `scripts/refresh_odds_hourly.ps1` |

### What does **not** use API credits

- Browsing game pages (reads repository)
- `GET /api/odds/today` poll from `game.js` (60s, no `refresh=true`)
- Dates already in `odds_repository/`
- Quota denied → stale repo served, no HTTP

**Game page in free mode:** Team market boxes show **—** (no sportsbook lines). The **center column** shows model pick, est. runs, edge, and O/U pick. A warning appears when `market_cards.source` is `none`.

---

## Game insights (Phase C)

**Endpoint:** `GET /api/games/mlb/{game_id}/insights?date=&use_cache=&refresh=`

Merges schedule game, `daily_board.json` slate row, filtered parlays, sportsbook `market_cards`, model block, and `highlights`.

### Game page layout (`/mlb/game/{id}`)

Three columns under the matchup header:

| Column | Content |
|--------|---------|
| **Away** | Logo, **Moneyline**, **Over/Under** (e.g. Over 8 -110), **Spread** (run line e.g. +1.5) — sportsbook only |
| **Center** | Model pick, win %, est. total runs (model), edge, confidence, O/U pick |
| **Home** | Logo, **Moneyline**, **Over/Under** (e.g. Under 8 -110), **Spread** — sportsbook only |

Stack order per team column: Moneyline → Over/Under → Spread.

Green highlight (`.market-pick-yes`) on the box the model agrees with: ML/spread on `pick_side`, total on `totals_pick` (over → away Over/Under box, under → home Over/Under box).

### `market_cards` sources (never model %)

| Field | Source |
|-------|--------|
| `away` / `home` `moneyline_american` | Odds repository or `mlb_odds_2025.csv` when `use_cache=true` |
| `total` `line`, `over_american`, `under_american` | Odds repository or `mlb_totals_2025.csv` |
| `away` / `home` `spread` | Odds repository (`spreads` from API snapshot) |
| Else | `null` / `—` in UI; `source: "none"` |

Model probabilities and estimated runs stay in the **`model`** block only — not in market stat boxes.

**Demo:** `/mlb/game/{id}?date=2025-08-15&use_cache=true` — historical ML/O/U from repository (if seeded) or CSV in team boxes.

---

## Odds repository (persistent snapshots)

Single source of truth: `data/processed/odds_repository/`

```
data/processed/odds_repository/
  index.json          # dates[], fetched_at, source, games_matched, api_fetch_count
  YYYY-MM-DD.json     # normalized games snapshot per date
```

### When the API is called

| Situation | HTTP? |
|-----------|-------|
| Date file exists, normal page load / game insights | **No** — read repository only |
| Date file missing, `USE_LIVE_ODDS=true` | **Yes** — once, then saved forever |
| Past date first request | **Yes** — historical endpoint once |
| `force_refresh=True` | **Yes** — overwrites that date’s file |
| API error but file exists | **No** — stale repository returned |

### Endpoints

| Type | URL |
|------|-----|
| **Live** (today/future) | `GET /v4/sports/baseball_mlb/odds` — ~1 credit |
| **Historical** (past) | `GET /v4/historical/sports/baseball_mlb/odds?date=YYYY-MM-DDT23:59:00Z` — ~10 × markets × regions ([docs](https://the-odds-api.com/liveapi/guides/v4/#get-historical-odds)) |

### Force refresh entry points

Only these pass `force_refresh=True` to the repository:

1. `scripts/morning_refresh.py` — `get_mlb_odds_for_date(today, force_refresh=True)` then board rebuild
2. `build_daily_board(..., refresh=True)` — via `attach_market_odds` (e.g. `/api/daily?refresh=true`)
3. **`/mlb/board` Run live / Refresh** — `/api/daily?live_test=true&refresh=true` (board bypass; see below)
4. `GET /api/games/mlb/{id}/insights?refresh=true`

Morning refresh avoids a **second** API call by passing `odds_force_refresh=False` into `build_daily_board` after the explicit odds refresh.

### Manual refresh today

```powershell
python scripts/morning_refresh.py
# or
curl "http://127.0.0.1:8000/api/daily?refresh=true"
# or game insights
curl "http://127.0.0.1:8000/api/games/mlb/{id}/insights?refresh=true"
```

### Seed from CSV (no API)

```powershell
python scripts/import_csv_to_odds_repository.py
```

Imports `mlb_odds_2025.csv` + `mlb_totals_2025.csv` with `source: csv_import`.

### Credit estimate

- **~1 credit** per new live date (first fetch)
- **~10–30 credits** per new past date (historical, depends on markets)
- **~1 credit** per manual daily refresh of today
- **0 credits** for repeated game page views, slate browsing, or dates already on disk

Optional env: `ODDS_REPOSITORY_DIR=` (default `data/processed/odds_repository`).

### API quota (hard limits)

All Odds API HTTP calls go through **`fetch_from_api_if_allowed()`** in `app/odds/odds_repository.py`. Nothing else may call the API directly.

Tracking file: `data/processed/odds_repository/quota.json`

```json
{
  "day": "2026-06-06",
  "day_count": 3,
  "hour_bucket": "2026-06-06T14",
  "hour_count": 2,
  "last_call_at": "2026-06-06T14:22:00+00:00",
  "last_denied_at": null,
  "last_denied_reason": null
}
```

| Rule | Default |
|------|---------|
| Max calls per **UTC calendar hour** | 20 (`ODDS_API_MAX_PER_HOUR`) |
| Max calls per **UTC calendar day** | 500 (`ODDS_API_MAX_PER_DAY`) |
| Successful HTTP only | Failed 4xx/5xx releases reserved slot (no credit counted) |
| Limit hit | No HTTP; stale `YYYY-MM-DD.json` served; warning in API/UI |

```mermaid
flowchart LR
  A[Request odds] --> B{Repo file exists?}
  B -->|yes, no force| C[Read disk — 0 credits]
  B -->|no or force_refresh| D{Quota OK?}
  D -->|no| E[Stale repo + warning]
  D -->|yes| F[HTTP once]
  F -->|success| G[Save repo + increment quota]
  F -->|fail| H[Release slot + stale repo]
```

### Hourly refresh

| Mechanism | Env / script |
|-----------|----------------|
| Task Scheduler / cron | `scripts/refresh_odds_hourly.ps1` |
| In-app loop (3600s) | `ODDS_HOURLY_REFRESH=true` + `USE_LIVE_ODDS=true` |

Hourly job calls `get_mlb_odds_for_date(today, force_refresh=True)` — still quota-gated. If denied, exits 0 with log (not a crash).

**Game page:** polls `GET /api/odds/today` every 60s (no `refresh=true`). When `fetched_at` or `board_generated_at` changes (e.g. after a board live test), reloads insights from disk — market lines + model picks, 0 extra API credits.

### Live board bypass (main-site sync)

Operator path on **`/mlb/board`** — normal browsing stays read-only (0 credits).

| Action | API | Effect |
|--------|-----|--------|
| **Run live** | `GET /api/daily?live_test=true&refresh=true` | Force Odds API fetch (quota-gated), write `odds_repository/YYYY-MM-DD.json`, rebuild `daily_board.json` with **full totals** (`skip_totals=false`) |
| **Refresh** (live mode) | Same | Re-pull lines and re-sync |
| **Demo** | `use_cache=true` | No bypass; historical only |

Main-site game pages (`/mlb/game/{id}`) pick up changes via `/api/odds/today` poll within ~60s. No per-game `?refresh=true` needed.

Requires `USE_LIVE_ODDS=true` and `ODDS_API_KEY` for a real HTTP pull; if quota denies, stale repository lines are kept and a warning appears on the board.

### Credit math (with quota)

- **~1 credit** per allowed live fetch (today refresh, hourly, morning, manual `?refresh=true`)
- **0 credits** for game page views, `/api/odds/today` poll, or reads when quota denies HTTP
- Hard cap: **20/hour UTC**, **500/day UTC** regardless of upgrade tier

---

## Live scores (Phase B)

**No API key required.** Live scores use the public [MLB Stats API](https://statsapi.mlb.com/) (`hydrate=linescore`).

**Requires:** `.\scripts\dev.ps1` running (or any uvicorn instance) — the browser polls the server every **60s**; the server caches MLB responses for **45s**.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/scores/today?sport=mlb` | All games today with scores + inning label (e.g. `Bot 7th`) |

Ticker on `/`, `/mlb`, and game pages auto-refreshes. Slate cards and game headers update on the same interval.

Morning schedule cache (`/api/schedule/mlb`, 6h TTL) is unchanged — live scores are a separate fast path.

---

## Morning automation (Phase 0)

Pre-build today's slate, odds, and model output without opening the browser or clicking **Run live**.

**Manual run** (from project root):

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/morning_refresh.py
```

Or via PowerShell wrapper (appends to log):

```powershell
.\scripts\morning_refresh.ps1
```

**Outputs**

| File | Purpose |
|------|---------|
| `data/processed/daily_board.json` | Cached board (`refresh=true`, includes O/U) |
| `data/processed/mlb_schedule_YYYY-MM-DD.json` | MLB schedule cache for UI |
| `data/processed/last_morning_refresh.json` | Last run status (time, ok, games, odds source) |
| `data/processed/morning_refresh.log` | Appended stdout/stderr from `.ps1` runs |

**Status API:** `GET /api/status/refresh` — returns `last_morning_refresh.json` or a default when missing.

**Odds API:** Only when `USE_LIVE_ODDS=true` and `ODDS_API_KEY` set. Each morning refresh then uses **~1 credit**. Otherwise morning refresh builds a **model-only** board (`odds_source: model_only`) — no API calls.

**Optional second run:** Schedule the same script again at **6:00 AM** local to catch overnight line posts.

**Optional ingest (separate task):** `python scripts/ingest_mlb.py` — run around **3–6 AM**, not at midnight; updates yesterday's results and rolling features (~5–15 min). Not part of `morning_refresh.ps1`.

### Windows Task Scheduler (12:01 AM daily)

The PC must be **on** at trigger time (sleep/hibernate may skip the task unless configured to wake).

1. Open **Task Scheduler** → **Create Task** (not Basic).
2. **General:** name e.g. `Parlay Builder Morning Refresh`; run whether user is logged on or not; highest privileges if needed for network.
3. **Triggers:** Daily at **12:01 AM** (local machine time).
4. **Actions:** Start a program  
   - Program: `powershell.exe`  
   - Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\Users\nickg\Documents\parlay-builder-v1\scripts\morning_refresh.ps1"`  
   - Start in: `C:\Users\nickg\Documents\parlay-builder-v1`
5. **Conditions:** adjust “Start only if on AC power” / wake settings as you prefer.
6. **Settings:** allow task to run on demand; do not stop after 72 hours.

After the first scheduled run, open `GET /api/daily` (no `refresh=true`) — today's board should load from cache.

**Morning vs `/api/daily` cache:** `morning_refresh` writes `skip_totals=false` (`…_live_totals_…` in `cache_key`). `/api/daily` live default is `skip_totals=true` (`…_no_totals_…`). When `refresh=false`, if an on-disk morning board exists for the same date (totals included, same edge/parlay settings) and is **&lt;24h** old, `/api/daily` and **Run live** on `/mlb/board` serve it without rebuilding — even when the O/U checkbox is unchecked.

**Test date override:** `python scripts/morning_refresh.py --date 2025-08-15`

---

## Morning checklist (MLB daily board)

Use this as the default daily workflow during the season. Phase 6 stays blocked until Phase 5 sign-off and advisor review of forward CLV.

| Step | When | Command / action |
|------|------|------------------|
| 1. Start server | Each session | `.\scripts\dev.ps1` (creates `.venv` if needed, installs deps, starts uvicorn on port 8000) |
| 2. Refresh game data | Morning, or after yesterday’s games finish | `python scripts/ingest_mlb.py` — updates scores, pitchers, rolling features (~5–15 min first run; faster when parquet cache warm) |
| 3. Open board | After server is up | http://127.0.0.1:8000/mlb — or `.\scripts\open_daily.ps1` to start server and open Home + MLB |
| 4. Load slate | On `/mlb` | **Run live** (today + The Odds API) or **Demo** (fixed date `2025-08-15` + historical odds CSV). Nothing loads until you click one. |
| 5. O/U toggle | Before Run | Check **O/U** to include totals model + sportsbook lines (slower on live). Unchecked = moneyline + parlays only. |
| 6. Refresh odds cache | Live board stale or lines moved | Click **Refresh** on `/mlb` (or `?refresh=true`). Board JSON cache TTL is **5 minutes** (`data/processed/daily_board.json`). |
| 7. Historical odds CSV | Weekly or when re-running market eval | `python scripts/load_mlb_odds_free.py` — only needed for demo mode, backtest, and `evaluate_mlb_market.py` (not live Odds API) |
| 8. Market eval (Phase 3) | After ingest + train + odds CSV | `python scripts/evaluate_mlb_market.py` — uses production `v3_logistic_pruned_platt`; see `MARKET.md` |
| 9. Backtest panel | Optional, bottom of `/mlb` | **Load saved** or **Run backtest** (30-day rolling report) |
| 10. Forward CLV backfill | Afternoon / near first pitch | `python scripts/backfill_forward_clv.py` — fills closing lines for morning +EV singles logged on **Run live** (`data/processed/forward_clv_log.jsonl`). Report: `GET /api/clv/summary?days=30` |

**Forward CLV log rules:** Live board only (`the_odds_api`). Cached board returns (5 min TTL) do not log. Same `pick_id` is skipped unless American odds move ≥5 points (then a new row is appended; latest row wins).

**Live vs demo**

| Mode | API | Needs |
|------|-----|--------|
| **Live** | `/api/daily` | `ODDS_API_KEY` in `.env`; `skip_totals=true` by default (uncheck O/U on UI for same) |
| **Demo** | `/api/daily?date=2025-08-15&use_cache=true` | Ingested games + `mlb_odds_2025.csv` (and `mlb_totals_2025.csv` if O/U checked) |

**Edge / parlay tuning (optional):** `/api/daily?min_edge=0.08&max_parlays=5` — mirrored on `/mlb` toolbar. Default **8%** edge matches `DEFAULT_MIN_EDGE` in `app/models/constants.py`.

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

Default edge threshold is **8%** (same as daily board). Override: `python scripts/evaluate_mlb_market.py --edge-threshold 0.05`

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

Defaults: 2–4 legs, cross-game only, `min_edge=8%`, top 5 parlays. See `PARLAY.md` for formulas and assumptions.

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

| Page | URL |
|------|-----|
| Home (no API) | http://127.0.0.1:8000/ |
| MLB board | http://127.0.0.1:8000/mlb |
| API (live) | http://127.0.0.1:8000/api/daily |
| API (demo) | http://127.0.0.1:8000/api/daily?date=2025-08-15&use_cache=true |
| Backtest (30d) | http://127.0.0.1:8000/api/backtest?days=30 |
| Saved backtest | http://127.0.0.1:8000/api/backtest/saved |

On `/mlb`, click **Run live** or **Demo** to load the board (no fetch on page load). Check **O/U** before Run to include totals. **Model accuracy** uses **Load saved** / **Run backtest** at the bottom.

Live mode requires `ODDS_API_KEY` in `.env`. The board caches responses for 5 minutes in `data/processed/daily_board.json` unless you click **Refresh** or pass `refresh=true`.

## MLB totals O/U (v1)

**Train** (requires Phase 1 games + totals odds CSV):

```powershell
python scripts/load_mlb_totals_odds_free.py
python scripts/train_mlb_totals.py
python scripts/backtest_mlb_totals_recent.py --days 7
python scripts/backtest_mlb_recent.py --days 30
```

**Rolling backtest API:** `GET /api/backtest?days=30` returns moneyline + totals metrics for the last N completed 2025 games (cached to `data/processed/mlb_backtest_report.json`). Requires `mlb_odds_2025.csv` and `mlb_totals_2025.csv`.

See `TOTALS.md` for metrics and production gate (log loss vs market, not accuracy %).

Demo dashboard includes O/U columns when `use_cache=true` and `mlb_totals_2025.csv` is present.

Fast moneyline-only board: `http://127.0.0.1:8000/api/daily?date=2025-08-15&use_cache=true&skip_totals=true`

**Live board** (default skips totals for speed): use **Run live** on `/mlb` without the O/U checkbox. API: `skip_totals=false` to include O/U on live odds.
