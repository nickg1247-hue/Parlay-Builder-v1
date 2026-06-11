# NBA predictions — weighted factor model

The **primary** NBA moneyline prediction is your **16-factor weighted model** (`app/data/nba_custom_weights.json`). A trained ML baseline is kept for comparison (`ml_prob_home` on the board).

## Your 16 factors (weights sum to 100%)

| Factor | Default weight |
|--------|---------------:|
| Team Offensive Rating | 15% |
| Team Defensive Rating | 15% |
| Starting Lineup Strength | 12% |
| Player Availability / Injuries | 12% |
| Recent Form (Last 10 Games) | 8% |
| Home Court Advantage | 7% |
| Bench Production | 6% |
| Matchup Advantages | 5% |
| Rest / Fatigue | 4% |
| Pace of Play Matchup | 3% |
| Rebounding Edge | 3% |
| Turnover Differential | 3% |
| Three-Point Shooting Efficiency | 3% |
| Free Throw Rate | 2% |
| Travel Situation | 1% |
| Coaching / Adjustments | 1% |

Each factor produces a **home edge** in [-1, +1]. The weighted sum is converted to **Weighted P(home)** via a sigmoid.

## Adjusting weights (UI)

1. Log in → **`/nba/board`**
2. Click **Factor weights** (opens **`/nba/board/factors`** — login required, not linked from public nav)
3. Use **▲ / ▼** on each factor to shift weight (always totals 100%)
4. **Save weights** → return to board and **Refresh** to see new predictions for **all games**

## Data sources

| Source | Factors |
|--------|---------|
| **stats.nba.com** (ingest + team stats cache) | ORtg, DRTG, pace, rebounding, TOV%, 3PT%, FT rate |
| **Rolling game history** | Recent form, rest/B2B, matchup proxies |
| **Fixed** | Home court (+1 edge) |
| **Neutral defaults** | Lineup/injury/bench/coaching/travel use matchup data or neutral until you add richer feeds |

## Setup

```powershell
.\.venv\Scripts\python.exe scripts\ingest_nba.py
.\.venv\Scripts\python.exe scripts\fetch_nba_team_stats.py
.\.venv\Scripts\python.exe scripts\bootstrap_nba.py
```

## UI

- **`/nba/board`** — Weighted P(home), link to factor weights, ML baseline column when trained
- **`/nba/board/factors`** — global weight editor (board only entry point)
- **`/nba/game/{id}`** — factor breakdown for a single matchup

## Demo vs live

- **Weighted P** — from your global factor weights + ingested stats
- **Bench P(home)** — demo-only fixed 54% benchmark
- **Market P(home)** — sportsbook lines when available

Spread/totals use separate experimental GBR models. Only **moneyline** uses the weighted factor stack.
