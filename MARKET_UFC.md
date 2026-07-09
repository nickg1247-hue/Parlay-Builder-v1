# UFC market comparison (Phase 3)

## Production model

| Item | Value |
|------|--------|
| **Artifact** | `data/processed/ufc_baseline_model.joblib` |
| **Manifest** | `data/processed/active_ufc_model.json` |
| **Holdout season** | **2024** (train 2021–2022, Platt 2023) |
| **Eval script** | `scripts/evaluate_ufc_market.py` |
| **Core module** | `app/odds/ufc_market_eval.py` |

Walk-forward backtest (`scripts/evaluate_ufc.py`) is **model-only** — market proof lives here.

## Edge threshold

| Setting | Value |
|---------|--------|
| **+EV flag** | **8%** (`DEFAULT_MIN_EDGE = 0.08`) |
| **Override** | `python scripts/evaluate_ufc_market.py --eval-only --edge-threshold 0.05` |

## Odds sources (priority)

| Priority | Source | Notes |
|----------|--------|--------|
| 1 | **Free CSV** | `data/processed/ufc_odds_2024.csv` — import via `scripts/load_ufc_odds_free.py` |
| 2 | **Demo fixture** | `data/fixtures/ufc_odds_2024_demo.csv` — imported by `scripts/bootstrap_ufc.py` |
| 3 | **Live repository** | `data/processed/ufc_odds_repository/YYYY-MM-DD.json` — snapshots from Run live |

**Free historical options (manual, no API credits):**

- [BestFightOdds](https://www.bestfightodds.com/) archives — export closing ML to CSV
- Community UFC odds datasets (Kaggle/GitHub) — normalize to `date, home_team, away_team, home_ml, away_ml`

**The Odds API (`mma_mixed_martial_arts`):** live `h2h` + optional `totals` (round O/U) via `fetch_live_ufc_odds(include_totals=True)`. **No bulk historical** endpoint wired for UFC.

## Import & evaluate

**Canonical CSV columns:** `date, home_team, away_team, home_ml, away_ml` (American odds).

```powershell
# Bulk import (MikeSpa ufc-master + demo)
python scripts/import_ufc_odds_bulk.py --with-demo

# Single file (canonical or BestFightOdds export)
python scripts/load_ufc_odds_free.py path\to\odds.csv

# Export unmatched 2024 fights for manual fill
python scripts/export_ufc_odds_gap.py

python scripts/evaluate_ufc_market.py --eval-only
```

**External datasets (manual download, no scraping in repo):**

| Dataset | Format | Import |
|---------|--------|--------|
| MikeSpa `ufc-master.csv` | `R_fighter,B_fighter,R_odds,B_odds,date` | `data/fixtures/ufc_odds_mikespa_master.csv` via bulk script |
| BestFightOdds archives | Export to canonical CSV | `load_ufc_odds_free.py` |
| jansen88 `complete_ufc_data.csv` | `favourite,underdog,favourite_odds,underdog_odds` | bulk script (auto-detect) |

**2024 match-rate blocker:** Public snapshots in-repo cover **2017–2021** (MikeSpa) plus **11 fights** on `2024-01-13` (demo). Full 2024 holdout (~562 fights) needs a completed BestFightOdds CSV — use `export_ufc_odds_gap.py` as a template. Target: **match_rate_pct > 70%** before trusting paper-trade ROI.

```powershell
python scripts/load_ufc_odds_free.py data\fixtures\ufc_odds_2024_demo.csv
python scripts/evaluate_ufc_market.py --eval-only
```

Outputs (gitignored): `data/processed/ufc_market_metrics.json`, `data/processed/ufc_2024_market_eval.csv`

API: `GET /api/ufc/market` (`?refresh=true`)

## Metrics reported

| Metric | Description |
|--------|-------------|
| `match_rate_pct` | 2024 holdout fights with valid moneylines |
| `log_loss_model` / `log_loss_market` | Vig-free market probs on matched subset |
| `plus_ev_picks` | Count with edge ≥ 8% on either side |
| `paper_trade_roi` | Flat $1 stake on +EV picks only |
| `ev_signal` | ROI > 0 (informational only) |

## Advisor stance — not betting-ready

**+EV paper-trade ROI does not prove edge until forward CLV.**

Forward log: `data/processed/forward_clv_ufc_log.jsonl`  
Backfill: `python scripts/backfill_forward_clv.py --sport ufc`  
Report: `GET /api/clv/summary?sport=ufc&days=30` (Performance page UFC tab)
