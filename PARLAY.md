# MLB parlay builder v1 (Phase 4)

**Experimental tooling — not betting advice.** Phase 3 did not prove a durable market edge. Use conservative filters and validate lines before any real wager.

## EV formula (v1)

For legs \(1..n\) from **different games** (cross-game only):

| Term | Formula |
|------|---------|
| Model joint probability | \(\prod_i P_{\text{model}}(\text{leg}_i)\) (independence) |
| Market joint probability | \(\prod_i P_{\text{market}}(\text{leg}_i)\) after per-leg vig removal |
| Decimal parlay payout | \(\prod_i \text{decimal}(\text{American odds}_i)\) |
| **EV** | \((P_{\text{model,joint}} \times \text{decimal payout}) - 1\) |

**Edge filter (default):** parlay EV ≥ `min_edge` (5%). Each game contributes at most one leg — the side with the largest positive single-game edge vs vig-free market.

## Assumptions

- Leg outcomes are **independent** (no same-game parlays, no correlation modeling).
- One median/consensus moneyline per game (The Odds API median across US books, or historical cache).
- Model is Phase 2 logistic regression with train-time imputation rules.
- Same-game parlays, live in-game odds, and correlation adjustments are **out of scope**.

## Run

```powershell
python scripts/rank_mlb_parlays.py
```

| Flag | Purpose |
|------|---------|
| `--date YYYY-MM-DD` | Target slate date |
| `--use-cache` | Historical odds from `mlb_odds_2025.csv` + slate from ingested games |
| `--min-edge 0.05` | Minimum parlay EV (default 5%) |
| `--max-parlays 5` | Top N parlays to return |

**Live odds:** set `ODDS_API_KEY` in `.env` ([The Odds API](https://the-odds-api.com) free tier). Without a key, use `--use-cache` on a date present in the free historical odds file.

**Output:** console table + `data/processed/mlb_parlays_today.json` (gitignored).
