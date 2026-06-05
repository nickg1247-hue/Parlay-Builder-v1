# Parlay Builder v1

Sports prediction and parlay optimization platform. Starting with **MLB**, expanding to NFL, NBA, college football, and NHL.

**Goal:** Keep the stack simple, measure everything, and beat generic prediction platforms through focused models and parlay expected-value (EV) ranking — not complexity.

---

## How we work

| Role | Agent | Responsibility |
|------|-------|----------------|
| **Advisor** | This agent | Roadmap, priorities, architecture decisions, trade-offs, success metrics, implementation briefs |
| **Coder** | Other helper | All code — data pipelines, models, APIs, UI, Git commits, debugging |

**Workflow**

1. Advisor recommends next step and locks decisions.
2. Advisor writes an **implementation brief** (goal, acceptance criteria, out of scope).
3. Coder implements and pushes to GitHub.
4. User reports back; advisor updates this README and sets the next milestone.

**Advisor does not write application code.** Coder does not change roadmap priorities without advisor alignment.

---

## Current status

| Item | Status |
|------|--------|
| **Phase** | **Phase 5 — MLB daily workflow** (primary focus before Phase 6 / next sport) |
| **MLB moneyline** | Production model **v3** (Platt + pruned Wave 1) — beats market on 2025 holdout log loss (see `MODEL.md`) |
| **MLB market / +EV** | Infrastructure done; **edge not proven** for live betting — see `MARKET.md` |
| **MLB parlays** | Phase 4 ranker + dashboard integration — **experimental** (see `PARLAY.md`) |
| **MLB totals O/U** | Built; **did not pass** production gate vs market — see `TOTALS.md` |
| **Last updated** | 2026-06-04 |

### Truth table (what “done” means)

| Track | Shipped? | Betting-ready? |
|-------|----------|----------------|
| Data ingest + features | Yes | — |
| Moneyline model v3 | Yes | Holdout metric passes; forward CLV still TBD |
| Market compare + paper trade | Yes | Conditional — modest paper ROI, weak vs market log loss in `MARKET.md` eval |
| Parlay EV ranker | Yes | No — use conservative filters |
| Daily dashboard (`/mlb`) | Yes (Phase 5 in progress) | Personal tool; polish + live workflow |
| Totals O/U | Yes (side track) | No — gate failed |

### Decisions locked

- [x] First sport: **MLB**
- [x] Advisor vs coder split defined
- [x] Git repository initialized and linked to GitHub
- [x] **Data budget:** $0 — free data sources only for now (optional `ODDS_API_KEY` for live board)
- [x] **V1 audience:** Local personal tool (PowerShell + localhost) until ready for public rollout
- [x] **Parlay v1 scope:** Cross-game only
- [x] **Edge hypothesis:** Stats + CLV tracking first; parlay EV math in Phase 4
- [x] Python 3.11 + venv, SQLite, FastAPI localhost app (see `DEV.md`)

### Decisions open

- [ ] Phase 5 exit: confirm daily morning workflow (ingest refresh cadence, live vs demo default)
- [ ] Better closing-line data before trusting Phase 3–4 signals (Odds API forward CLV vs SBR proxy)
- [ ] Phase 6 sport order after MLB Phase 5 sign-off (default: NBA → NFL → CFB → NHL)

---

## Architecture (target — keep simple)

```
Data in → Features → Model → Predictions → Parlay EV ranker → UI
              ↑                    ↑
         Historical            Live odds (API)
```

| Layer | Purpose |
|-------|---------|
| **Prediction core** | Win probability per game (moneyline first) |
| **Parlay optimizer** | Rank multi-leg parlays by EV vs sportsbook implied probability |
| **UI** | Daily slate, model vs market, top EV singles and parlays |

**Stack:** Python + SQLite, local dev server (localhost via PowerShell). Free data until budget increases. No microservices.

**Detail docs:** [`DEV.md`](DEV.md) · [`MODEL.md`](MODEL.md) · [`MARKET.md`](MARKET.md) · [`PARLAY.md`](PARLAY.md) · [`TOTALS.md`](TOTALS.md)

---

## Success metrics

We do not add features or sports until these are measured on holdout/backtest data.

| Metric | What it tells us |
|--------|------------------|
| **Log loss / Brier score** | Calibration of win probabilities |
| **CLV (closing line value)** | Whether our line beats the market close over time |
| **Paper-trade ROI** | Simulated returns on flagged +EV plays |
| **Parlay EV** | `our_joint_prob × payout − 1` vs book implied joint prob |

**Phase gate:** Baseline model must beat naive baseline (home win rate + simple Elo) before adding odds comparison or parlays. **Met** for moneyline v3 on 2025 holdout.

---

## Roadmap

### Phase 0 — Foundation & decisions ✅

**Objective:** Lock scope, repo hygiene, and environment.

| Task | Status |
|------|--------|
| Git + GitHub connected | Done |
| `.gitignore` (secrets, data, venv) | Done |
| This README / roadmap | Done |
| Lock data budget ($0 + optional live key) | Done |
| Lock V1 audience (local personal tool) | Done |
| Python env + project layout | Done |
| `.env.example` | Done |
| Data sources documented | Done (`DEV.md`) |

---

### Phase 1 — MLB data foundation ✅

**Objective:** Reliable labeled dataset — one row per game.

| Task | Status |
|------|--------|
| Historical game results (2023–2025) | Done |
| Starting pitchers / ERA | Done (FIP null — documented) |
| Team stats (rolling form, home/away, park, rest) | Done |
| Daily schedule ingestion | Done |
| Data stored locally (not in Git) | Done |
| Validation script | Done |

---

### Phase 2 — MLB baseline model ✅

**Objective:** Simple model that beats naive baseline on holdout.

| Task | Status |
|------|--------|
| Season-based holdout (2023–24 train · 2025 test) | Done |
| Baseline: home win rate + Elo | Done |
| Model iterations (v1 → Wave 1 → ablation → **v3 Platt**) | Done |
| Log loss, Brier, accuracy reporting | Done |
| Phase gate vs market | Done (v3) |

**Sub-phases (changelog):** 2.6 Wave 1 (no market beat) · 2.7 ablation + Platt (**production**). See `MODEL.md`.

---

### Phase 3 — Market comparison (MLB moneyline) ✅ (conditional)

**Objective:** Compare model to implied probabilities; track CLV / paper trade.

| Task | Status |
|------|--------|
| Free historical odds (SBR dataset) | Done |
| Vig removal + +EV flags | Done |
| Paper-trade simulation | Done |
| Live odds stub (`ODDS_API_KEY`) | Done |
| Durable edge proven | **No** — proceed with caution (`MARKET.md`) |

**Exit criteria (revised):** Infrastructure runs daily; **do not** treat +EV as strong edge until closing-line quality improves.

---

### Phase 4 — MLB parlay builder (v1) ✅ (experimental)

**Objective:** Rank cross-game parlays by EV.

| Task | Status |
|------|--------|
| Moneyline odds on slate | Done |
| 2–4 leg cross-game combinations | Done |
| Independence joint prob + EV rank | Done |
| CLI + JSON output | Done |
| Wired into daily board API | Done |

**Rules:** Cross-game only; rank by EV. See `PARLAY.md`.

---

### Phase 5 — Minimal UI / daily workflow 🔄 **current**

**Objective:** Usable morning tool on localhost before expanding sports.

| Task | Status |
|------|--------|
| FastAPI app + health | Done |
| Today’s MLB slate (`/mlb`) | Done |
| Model prob vs market (moneyline) | Done |
| Top +EV singles + parlays on board | Done |
| Optional O/U on board (`skip_totals` / checkbox) | Done |
| Backtest panel (30d moneyline + totals) | Done |
| `open_daily.ps1` one-click browser | Done |
| UI filters (min EV, max legs) configurable in browser | **Partial** — thresholds mostly fixed in API/UI |
| Documented “every morning” refresh steps | **Partial** — see `DEV.md`; ingest cadence TBD |
| Advisor sign-off on Phase 5 exit | **Not started** |

**Exit criteria:** Open app each morning; see slate, edges, and parlays with a repeatable refresh path (live key or demo cache). **Close this phase before Phase 6.**

**Suggested Phase 5 finish (advisor):**

1. Lock default workflow: demo vs live, when to re-run `ingest_mlb.py`.
2. Optional UI: min EV / max parlay legs without editing code.
3. Short “Morning checklist” block in `DEV.md` linked from here.

---

### Phase 6 — Expand sports ⏸️ (after MLB Phase 5)

**Default order** (revise after MLB daily workflow is signed off):

| Order | Sport | Parlay module? | Notes |
|-------|-------|----------------|-------|
| 1 | **MLB** | Yes | Finish Phase 5 first |
| 2 | **NBA** | Yes | High daily volume |
| 3 | **NFL** | Yes | Weekly slate |
| 4 | **College football** | Later | Higher variance |
| 5 | **NHL** | Later | Thinner markets |

Each sport repeats data → model → market → parlay; reuse odds + EV engine.

---

## Implementation brief template

When ready to build, advisor provides this to the coder:

```markdown
## Implementation brief

**Goal:** [one sentence]

**Acceptance criteria:**
- [ ] ...
- [ ] ...

**Out of scope:**
- ...

**Suggested approach:** [high-level, not code]

**Dependencies / decisions locked:**
- ...
```

---

## Changelog

| Date | Phase | Update |
|------|-------|--------|
| 2026-06-03 | 0 | README created; MLB first sport; Git linked |
| 2026-06-03 | 0 | Locked: $0 budget, localhost UI, cross-game parlays |
| 2026-06-04 | 2.6 | Wave 1 features — did not beat market; v1 kept |
| 2026-06-04 | 2.7 | Ablation + Platt — **v3 promoted** (log loss 0.6762) |
| 2026-06-04 | Totals | O/U model + dashboard — gate not passed |
| 2026-06-04 | 5 | README synced: Phases 0–4 done; **Phase 5 active** before next sport |

*Advisor updates this table when phases start, complete, or priorities change.*

---

## Repo hygiene

- **Never commit:** `.env`, API keys, credentials, large datasets (`data/raw/`, `*.csv`, `*.db`)
- **Do commit:** code, configs, `.env.example`, docs, advisor skill (`.cursor/skills/`)
- **Data lives locally** or in cloud storage — not in Git

---

## Links

- **GitHub:** https://github.com/nickg1247-hue/Parlay-Builder-v1
- **Advisor skill:** `.cursor/skills/parlay-builder-advisor/SKILL.md`
- **Run locally:** `.\scripts\dev.ps1` → http://127.0.0.1:8000/mlb
