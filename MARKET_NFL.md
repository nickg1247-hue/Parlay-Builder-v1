# NFL market comparison (Phase N3)

## Production model

| Item | Value |
|------|--------|
| **Artifact** | `data/processed/nfl_baseline_model.joblib` |
| **Manifest** | `data/processed/active_nfl_model.json` |
| **Holdout** | 2025 preseason + regular season |
| **Inference** | `predict_home_win_proba()` from `app/models/nfl_baseline.py` |
| **Eval script** | `scripts/evaluate_nfl_market.py` |

See `NFL_MODEL.md` for feature list and gates.

## Edge threshold

| Setting | Value |
|---------|--------|
| **+EV flag** | **8%** (`DEFAULT_MIN_EDGE = 0.08`) — same as MLB/NBA/CFB |
| **Override** | `python scripts/evaluate_nfl_market.py --edge-threshold 0.05` |

## Odds sources (priority)

| Priority | Source | Notes |
|----------|--------|--------|
| 1 | **Live repository** | `data/processed/nfl_odds_repository/YYYY-MM-DD.json` — Odds API snapshots |
| 2 | **ESPN scoreboard lines** | `espn_home_ml` / `espn_away_ml` / `espn_spread` / `espn_ou` when the live/day scoreboard publishes them. Historical week pulls usually omit BET odds (0/2151 on the 2019–2025 ingest). |

**The Odds API keys:** `americanfootball_nfl` (regular) and `americanfootball_nfl_preseason`. Live/today/future only via `app/odds/nfl_odds_repository.py` → shared quota gate. **No bulk historical** burn.

**Team match:** Odds API full names → ESPN abbr via `normalize_nfl_team` (`app/odds/nfl_team_aliases.py`). Franchise aliases: OAK→LV, WAS→WSH.

## Match rate

Re-run eval after ingest (ESPN lines on completed games) or after capturing Odds API snapshots:

```powershell
python scripts/evaluate_nfl_market.py
```

Outputs (gitignored): `data/processed/nfl_market_metrics.json`

## Metrics reported

| Metric | Description |
|--------|-------------|
| `matched_games` | Holdout games with valid moneylines |
| `model_log_loss` / `market_log_loss` | Moneyline calibration vs vig-free market |
| `plus_ev_picks` | Count at 8% edge vs market |
| `paper_pnl_units` | Flat $1 paper profit on +EV picks |

## Advisor stance

Paper-trade PnL on matched holdout is **not betting-ready**. Forward CLV capture during live season required before any real-money claim (`betting_ready: false`).

## Scripts

```powershell
python scripts/evaluate_nfl_market.py
python scripts/evaluate_nfl_market.py --edge-threshold 0.05
```
