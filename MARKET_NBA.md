# NBA market comparison (Phase 3)

## Production model

| Item | Value |
|------|--------|
| **Artifact** | `data/processed/nba_baseline_model.joblib` |
| **Manifest** | `data/processed/active_nba_model.json` |
| **Version** | `v1_logistic` |
| **Inference** | `predict_home_win_proba()` from `app/models/nba_baseline.py` |
| **Eval script** | `scripts/evaluate_nba_market.py` |

See `DEV.md` (NBA baseline) for holdout log loss (~0.614 on 2025-26).

## Edge threshold

| Setting | Value |
|---------|--------|
| **+EV flag** | **8%** (`DEFAULT_MIN_EDGE = 0.08`) — same as MLB |
| **Override** | `python scripts/evaluate_nba_market.py --edge-threshold 0.05` |

## Odds sources (priority)

| Priority | Source | Notes |
|----------|--------|--------|
| 1 | **Free CSV** | `data/processed/nba_odds_2026.csv` — import via `scripts/load_nba_odds_free.py` |
| 2 | **Live repository** | `data/processed/nba_odds_repository/YYYY-MM-DD.json` — snapshots from quota-gated live pulls only |

**Free historical options (manual, no API credits):**

- [SportsBookReview NBA archives](https://www.sportsbookreviewsonline.com/scoresoddsarchives/nba/nbaoddsarchives.htm) — export closing/opening ML to CSV
- Community scrapers (e.g. [flancast90/sportsbookreview-scraper](https://github.com/flancast90/sportsbookreview-scraper)) — convert output to `date, home_team, away_team, home_ml, away_ml`

**The Odds API (`basketball_nba`):** **live/today only** via `app/odds/nba_odds_repository.py` → shared quota gate (`fetch_from_api_if_allowed` pattern). **No bulk historical** endpoint usage for NBA eval.

## Match rate

Depends on imported CSV + any live-captured repository dates overlapping 2025-26 holdout. Re-run eval after importing odds:

```powershell
python scripts/load_nba_odds_free.py path\to\nba_odds.csv
python scripts/evaluate_nba_market.py
```

Outputs (gitignored): `data/processed/nba_market_eval.json`, `data/processed/nba_2026_market_eval.csv`

## Metrics reported

| Metric | Description |
|--------|-------------|
| `match_rate_pct` | Holdout games with valid moneylines |
| `log_loss_model` / `log_loss_market` | Vig-free market probs on matched subset |
| `plus_ev_picks` | Count with edge ≥ 8% on either side |
| `paper_trade_roi` | Flat $1 stake on +EV picks only |
| `ev_signal` | ROI > 0 (informational only) |

## Advisor stance — not betting-ready

**+EV paper-trade ROI does not prove edge until forward CLV.**

- Free SBR / CSV lines are not verified closing lines.
- Live repository snapshots are opening/prop lines unless captured near tip.
- **NBA-CLV** (next phase) must log picks at decision time and compare to a closing proxy before any real-money claim.
- Do **not** treat `ev_signal: true` as production-ready.

## Scripts

```powershell
python scripts/load_nba_odds_free.py your_nba_odds.csv
python scripts/evaluate_nba_market.py
pytest tests/test_nba_market_eval.py -q
```

## Forward CLV (NBA-CLV)

**Not betting-ready** until advisor reviews forward CLV (`betting_ready: false` on all log rows and summaries).

| Item | Value |
|------|--------|
| **Log file** | `data/processed/forward_clv_nba_log.jsonl` (separate from MLB `forward_clv_log.jsonl`) |
| **Pick id** | `nba:{board_date}:{game_id}:{side}` |
| **Edge threshold** | 8% (`DEFAULT_MIN_EDGE`) |
| **Morning log** | `/nba/board` **Run live** or `GET /api/nba/daily?refresh=true` when `USE_LIVE_ODDS=true` — logs +EV singles from live `the_odds_api_live` odds |
| **Afternoon backfill** | `python scripts/backfill_forward_clv.py --sport nba` — quota-gated live `basketball_nba` only; no bulk historical |
| **Dry run** | `python scripts/backfill_forward_clv.py --sport nba --dry-run` |
| **Report** | `GET /api/clv/summary?sport=nba&days=30` |

**Workflow**

1. Morning: open [/nba/board](http://127.0.0.1:8000/nba/board) → **Run live** (or `GET /api/nba/daily?refresh=true`) after lines are up.
2. Afternoon / near tip: `python scripts/backfill_forward_clv.py --sport nba`
3. Review `/api/clv/summary?sport=nba&days=30` before any real-money claim.

**Sample log row**

```json
{
  "sport": "nba",
  "pick_id": "nba:2026-06-10:401766458:home",
  "board_date": "2026-06-10",
  "game_id": "401766458",
  "team": "Orlando Magic",
  "side": "home",
  "american_odds_at_pick": 105,
  "edge_at_pick": 0.10,
  "market_prob_at_pick": 0.45,
  "odds_source": "the_odds_api_live",
  "betting_ready": false,
  "close_american_odds": null,
  "close_status": null
}
```
