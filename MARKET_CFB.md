# CFB market comparison (Phase C3)

## Production model

| Item | Value |
|------|--------|
| **Artifact** | `data/processed/cfb_baseline_model.joblib` |
| **Manifest** | `data/processed/active_cfb_model.json` |
| **Holdout** | 2025 regular season |
| **Inference** | `predict_home_win_proba()` from `app/models/cfb_baseline.py` |
| **Eval script** | `scripts/evaluate_cfb_market.py` |

See `CFB_MODEL.md` for walk-forward proof and feature list.

## Edge threshold

| Setting | Value |
|---------|--------|
| **+EV flag** | **8%** (`DEFAULT_MIN_EDGE = 0.08`) — same as MLB/NBA |
| **Override** | `python scripts/evaluate_cfb_market.py --edge-threshold 0.05` |

## Odds sources (priority)

| Priority | Source | Notes |
|----------|--------|--------|
| 1 | **CFBD `/lines`** | Spread + O/U (+ ML when providers publish) — cached under `data/processed/cfb_lines_cache/` |
| 2 | **Live repository** | `data/processed/cfb_odds_repository/YYYY-MM-DD.json` — Odds API snapshots (`americanfootball_ncaaf`) |

**The Odds API:** live/today only via `app/odds/cfb_odds_repository.py` → shared quota gate. **No bulk historical** burn in v1 eval.

**Game crosswalk:** ESPN slate `game_id` ≠ CFBD id. Lines attach by `(date, normalize_team_name(home), normalize_team_name(away))` — see `app/odds/cfb_game_match.py`.

## Match rate

Re-run eval after CFBD lines cache populates (live slate dates with `CFBD_API_KEY`) or after capturing Odds API snapshots:

```powershell
python scripts/evaluate_cfb_market.py
```

Outputs (gitignored): `data/processed/cfb_market_metrics.json`, `data/processed/cfb_market_eval.csv`

## Metrics reported

| Metric | Description |
|--------|-------------|
| `match_rate_pct` | Holdout games with any odds matched (spread/O/U/ML) |
| `ml_match_rate_pct` | Holdout games with valid moneylines |
| `log_loss_model` / `log_loss_market` | Moneyline calibration vs vig-free market |
| `brier_model` / `accuracy_model` | Model holdout quality on matched ML games |
| `plus_ev_picks` | Count at 8% edge vs market |
| `paper_trade_roi` | Flat $1 paper ROI on +EV picks |
| `spread_cover_log_loss` | Home-cover log loss at CFBD median spread |
| `totals_over_log_loss` | Over log loss at CFBD median O/U |

## Advisor stance

Paper-trade ROI on matched holdout is **not betting-ready**. Forward CLV capture during live season required before any real-money claim (`betting_ready: false`).

## Latest holdout eval (2025, CFBD lines cache)

| Metric | Value |
|--------|-------|
| Holdout games | 888 |
| Matched (any line) | 888 (100%) |
| Matched ML | 543 (61.15%) |
| Log loss — model / market | 0.6749 / 0.6206 |
| Brier / accuracy | 0.2377 / 61.51% |
| +EV picks (8% edge) | 353 |
| Paper ROI | +2.95% (10.41 units) |
| Spread cover log loss | 1.18 |
| Totals over log loss | 0.73 |

Reproduce: `python scripts/fetch_cfb_holdout_lines.py --season 2025` then `python scripts/evaluate_cfb_market.py`.

**Note:** Market beats model on log loss; paper ROI on +EV picks is not betting-ready without forward CLV.

## Scripts

```powershell
python scripts/fetch_cfb_holdout_lines.py --season 2025
pytest tests/test_cfb_game_match.py tests/test_cfb_odds_repository.py tests/test_cfb_market_eval.py -q
python scripts/evaluate_cfb_market.py
```

## Live slate wiring

When `USE_LIVE_ODDS=true` and `ODDS_API_KEY` set:

- `GET /api/cfb/predictions?date=` includes `home_ml`, `away_ml`, `home_spread_point`, `spread_line_source` (`book` | `proxy`), `ev_home` / `ev_away`, `plus_ev_ml`
- CFBD crosswalk improves `ou_line_source: book` on Saturdays
- Spread picks use book line when available; proxy **-7** only as fallback
