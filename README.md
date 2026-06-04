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
| **Phase** | Phase 0 — Project shell (in progress) |
| **Active sport** | MLB (in season) |
| **Git / GitHub** | Connected — [Parlay-Builder-v1](https://github.com/nickg1247-hue/Parlay-Builder-v1) |
| **Last updated** | 2026-06-03 |

### Decisions locked

- [x] First sport: **MLB**
- [x] Advisor vs coder split defined
- [x] Git repository initialized and linked to GitHub
- [x] **Data budget:** $0 — free data sources only for now
- [x] **V1 audience:** Local personal tool (PowerShell + localhost) until ready for public rollout
- [x] **Parlay v1 scope:** Cross-game only
- [x] **Edge hypothesis:** Stats + CLV tracking first; parlay EV math in Phase 4

### Decisions open (Phase 0)

- [ ] None blocking Phase 0 shell — proceed with project scaffold

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

**Stack:** Python + SQLite, local dev server (localhost via PowerShell). Free data only until budget increases. No microservices.

---

## Success metrics

We do not add features or sports until these are measured on holdout/backtest data.

| Metric | What it tells us |
|--------|------------------|
| **Log loss / Brier score** | Calibration of win probabilities |
| **CLV (closing line value)** | Whether our line beats the market close over time |
| **Paper-trade ROI** | Simulated returns on flagged +EV plays |
| **Parlay EV** | `our_joint_prob × payout − 1` vs book implied joint prob |

**Phase gate:** Baseline model must beat naive baseline (home win rate + simple Elo) before adding odds comparison or parlays.

---

## Roadmap

### Phase 0 — Foundation & decisions

**Owner:** Advisor (decisions) + Coder (scaffold)

**Objective:** Lock scope, repo hygiene, and environment so Phase 1 can start cleanly.

| Task | Owner | Status |
|------|-------|--------|
| Git + GitHub connected | Coder | Done |
| `.gitignore` (secrets, data, venv) | Coder | Done |
| This README / roadmap | Advisor | Done |
| Lock data budget | Advisor + User | Not started |
| Lock V1 audience (personal vs public) | Advisor + User | Not started |
| Choose Python env + project layout | Coder | Not started |
| Add `.env.example` (no secrets) | Coder | Not started |
| Document required data sources (MLB) | Advisor | Not started |

**Exit criteria:** Open decisions resolved; coder has empty project structure ready for data ingest.

---

### Phase 1 — MLB data foundation

**Owner:** Coder (implement) · Advisor (spec + review)

**Objective:** Reliable labeled dataset — one row per game, no model yet.

| Task | Owner | Status |
|------|-------|--------|
| Historical game results (2+ seasons) | Coder | Not started |
| Starting pitchers / probable starters | Coder | Not started |
| Team stats (recent form, home/away) | Coder | Not started |
| Daily schedule ingestion | Coder | Not started |
| Data stored locally (not in Git) | Coder | Not started |
| Validation: row count, date ranges, no duplicate game IDs | Coder | Not started |

**MLB features (v1 — prioritize in this order)**

1. Starting pitcher quality (ERA / FIP / xFIP or season aggregates)
2. Team recent form (last 10–20 games, run differential)
3. Home / away
4. Rest days / day-after-night-game flag
5. Head-to-head or platoon splits *(optional v1.1)*

**Out of scope:** Player props, live in-game, weather API, deep learning.

**Exit criteria:** Script/notebook produces a clean modeling table with home-win label for backtest window.

---

### Phase 2 — MLB baseline model

**Owner:** Coder (implement) · Advisor (metrics review)

**Objective:** Simple model that beats naive baseline on holdout data.

| Task | Owner | Status |
|------|-------|--------|
| Train/test split (season-based holdout) | Coder | Not started |
| Baseline: home win rate + Elo | Coder | Not started |
| Model v1: logistic regression or gradient boosting | Coder | Not started |
| Report log loss, Brier score, accuracy | Coder | Not started |
| Advisor review vs phase gate | Advisor | Not started |

**Exit criteria:** Model v1 beats baseline on holdout; probabilities are reasonably calibrated.

---

### Phase 3 — Market comparison (MLB moneyline)

**Owner:** Coder (implement) · Advisor (EV logic review)

**Objective:** Compare model probabilities to sportsbook implied probabilities; track CLV.

| Task | Owner | Status |
|------|-------|--------|
| Integrate odds source (API per data budget) | Coder | Not started |
| Remove vig from implied probabilities | Coder | Not started |
| Flag +EV single-game moneyline plays | Coder | Not started |
| Log picks vs closing line (CLV tracking) | Coder | Not started |
| Paper-trade simulation (flat stake) | Coder | Not started |

**Exit criteria:** CLV tracking runs daily; advisor confirms edge signal before Phase 4.

---

### Phase 4 — MLB parlay builder (v1)

**Owner:** Coder (implement) · Advisor (parlay rules review)

**Objective:** Rank daily cross-game parlays by expected ROI, not “most likely to hit.”

| Task | Owner | Status |
|------|-------|--------|
| Pull moneyline odds for full daily slate | Coder | Not started |
| 2–4 leg cross-game combinations | Coder | Not started |
| Joint probability from model (independence assumption v1) | Coder | Not started |
| EV ranking vs best available book price | Coder | Not started |
| Output: EV %, est. hit rate, legs, book | Coder | Not started |
| Minimum EV threshold filter | Coder | Not started |

**Parlay v1 rules**

- Cross-game only (no same-game parlays until correlation logic exists)
- Rank by **EV**, not hit rate
- Show both model joint prob and book implied joint prob

**Out of scope:** Same-game parlays, auto-betting, live odds.

**Exit criteria:** Daily ranked parlay list with documented EV math; advisor sign-off.

---

### Phase 5 — Minimal UI / daily workflow

**Owner:** Coder (implement) · Advisor (UX scope)

**Objective:** Usable daily tool — personal use first unless V1 audience decision says public.

| Task | Owner | Status |
|------|-------|--------|
| Today’s MLB slate view | Coder | Not started |
| Model prob vs market implied prob | Coder | Not started |
| Top +EV singles and parlays | Coder | Not started |
| Simple filters (min EV, max legs) | Coder | Not started |

**Exit criteria:** User can open app/site each morning and see actionable MLB output.

---

### Phase 6 — Expand sports (order TBD after MLB proven)

**Default expansion order** (advisor recommendation — revise when Phase 4 completes):

| Order | Sport | Parlay module? | Notes |
|-------|-------|----------------|-------|
| 1 | **MLB** | Yes | Current focus |
| 2 | **NBA** | Yes | High daily volume, parlay-friendly |
| 3 | **NFL** | Yes | Weekly slate; add before or with CFB based on season |
| 4 | **College football** | Later | Higher variance; messier data |
| 5 | **NHL** | Later | Thinner markets |

Each new sport repeats Phases 1–4 with sport-specific features. Reuse shared odds + EV engine.

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
| 2026-06-03 | Phase 0 | README created; MLB chosen as first sport; Git linked to GitHub |
| 2026-06-03 | Phase 0 | Locked: $0 budget, local localhost UI, cross-game parlays, stats-first edge |
| | | |

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
