# MLB market comparison (Phase 3)

## Odds source

| Item | Detail |
|------|--------|
| **Dataset** | [mlb-odds-scraper release `dataset`](https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset) |
| **File** | `mlb_odds_dataset.json` (~76 MB) |
| **Origin** | SportsBookReview historical lines collected by community scraper (not live sportsbook scraping by this project) |
| **Books** | Median `currentLine` across BetMGM, FanDuel, Caesars, Bet365, DraftKings, BetRivers |
| **License** | Follow [repository license](https://github.com/ArnavSaraogi/mlb-odds-scraper) (MIT); MLB/SBR data subject to their terms |

**Line type note:** We use SBR `currentLine` median as a **closing proxy**, not verified official closing lines. Opening lines are also in the JSON. This limits CLV claims until true closing or The Odds API historical (paid) data is added.

**Data hygiene:** American odds must satisfy `100 ≤ |odds| ≤ 500` to drop scraper placeholders (e.g. `-1`, `-2`).

## 2025 holdout match rate

| Metric | Value |
|--------|-------|
| Holdout games | 2,430 |
| Matched with odds | 1,758 |
| **Match rate** | **72.35%** |

Unmatched games: missing from SBR export, invalid odds, or team/date alignment gaps.

## Summary metrics (edge threshold 2%)

| Metric | Value |
|--------|-------|
| Log loss — model | 0.6797 |
| Log loss — market (vig-free) | 0.6770 |
| Model beats market (log loss) | No (slightly worse) |
| +EV picks flagged | 1,403 |
| Paper-trade ROI (flat $1) | **5.92%** (83.02 units) |
| +EV hit rate | 48.5% |
| EV signal (ROI > 0) | Yes (weak) |

## Advisor recommendation

**Conditional — do not treat as strong edge yet.**

- Paper ROI is modestly positive on flagged plays but **calibration vs market is not better** (log loss slightly worse than vig-free market implied).
- Lines are median SBR `currentLine`, not confirmed closings; +EV count is high (~80% of matched games), suggesting miscalibration or line-type mismatch rather than a durable 6% edge.
- **Suggested path:** Proceed to Phase 4 only for **infrastructure** (EV ranking plumbing) while planning better closing-line data (e.g. user-supplied Odds API key for forward CLV, or updated free snapshots). Re-run evaluation after odds quality improvements.

## Scripts

```powershell
python scripts/load_mlb_odds_free.py
python scripts/evaluate_mlb_market.py
```

Outputs (gitignored): `data/processed/mlb_odds_2025.csv`, `data/processed/mlb_2025_market_eval.csv`, `data/processed/mlb_market_metrics.json`
