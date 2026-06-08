# Coder agent prompts

Copy everything inside a phase block (from the opening line through **End of prompt**) and paste into the coder agent. Work one phase at a time; do not start the next until the current phase is done and verified.

---

## Phase 0 — Morning automation

**Start copying below this line.**

```
Implement Phase 0 from ROADMAP.md — Morning automation.

GOAL
Add automatic morning refresh so today's MLB slate, odds, and model output are pre-built and cached before anyone opens the site. No browser or manual "Run live" required.

READ FIRST
- ROADMAP.md (Phase 0)
- DEV.md
- app/services/daily_board.py
- app/parlay/slate.py
- app/main.py
- scripts/backfill_forward_clv.py (script pattern)

OUT OF SCOPE
- ESPN UI (Phase A) — no new HTML pages
- Windows Task Scheduler on the user's machine — document steps in DEV.md only
- ingest_mlb.py scheduling — document as optional separate task only
- In-app APScheduler inside FastAPI
- Player props, live ticker, news

ACCEPTANCE CRITERIA
- scripts/morning_refresh.py runs from project root and calls build_daily_board(game_date=date.today(), use_cache=False, refresh=True, skip_totals=False) with default edge/parlay settings from app/models/constants.py and app/parlay/ev_ranker.py
- On success: writes/updates data/processed/daily_board.json (existing _write_cache behavior)
- On success: writes data/processed/last_morning_refresh.json with at least: ran_at (ISO UTC), ok (bool), date, games_on_slate, odds_source, error (null on success)
- On failure: logs error, sets ok: false in last_morning_refresh.json, does NOT wipe a good existing daily_board.json, exits non-zero
- One HTTP retry (30s backoff) on transient network errors when building the board
- If ODDS_API_KEY is missing: still builds model-only board, logs clear warning, records odds_source accordingly
- scripts/morning_refresh.ps1 activates .venv, runs Python script, appends stdout/stderr to data/processed/morning_refresh.log
- app/services/schedule_mlb.py provides refresh_schedule_cache(game_date) using MLB Stats API (sportId=1, hydrate=probablePitcher). Writes data/processed/mlb_schedule_{YYYY-MM-DD}.json with: game_id, home_team, away_team, home_team_id, away_team_id, start_time_utc, status, home_score, away_score. Called from morning_refresh.py after daily board step
- GET /api/status/refresh returns last_morning_refresh.json or sensible default if missing
- tests/test_morning_refresh.py covers: success (mock build_daily_board + schedule), failure preserves cache, status API returns JSON
- DEV.md updated: manual run, log path, optional 6:00 AM second run, Windows Task Scheduler for 12:01 AM daily (powershell -File scripts\morning_refresh.ps1), PC must be on, Odds API quota note
- pytest passes; no regressions to existing daily board tests

APPROACH
1. scripts/morning_refresh.py — argparse optional --date for testing; mirror backfill_forward_clv.py structure
2. app/services/schedule_mlb.py — reuse app/parlay/slate.py patterns (fetch_mlb_schedule_day, normalize_team_name)
3. app/main.py — GET /api/status/refresh
4. Tests — tmp_path + monkeypatch (same style as tests/test_forward_clv.py)
5. Minimal diff; match existing code style

LOCKED DECISIONS
- skip_totals=False (include O/U in morning cache)
- refresh=True (bypass 5-min TTL)
- Reuse build_daily_board; do not duplicate parlay/odds logic
- Morning time 12:01 AM local is user Task Scheduler config; script is timezone-agnostic

VERIFY
.\.venv\Scripts\Activate.ps1
python scripts/morning_refresh.py
pytest tests/test_morning_refresh.py -q
pytest -q

DO NOT
- Create git commits unless the user asks
- Change parlay edge thresholds or model code
- Build Phase A UI yet

When done, report: files changed, sample last_morning_refresh.json, and Task Scheduler steps for the user.
```

**End of prompt.**

---

## Phase A — ESPN shell (MLB)

**Start copying below this line.**

```
Implement Phase A from ROADMAP.md — ESPN shell (MLB).

PREREQUISITE
Phase 0 complete: morning_refresh.py, schedule cache, /api/status/refresh working.

GOAL
Home page → MLB game list with logos → game detail layout. Site reads morning cache on first load without clicking Run live.

READ FIRST
- ROADMAP.md (Phase A)
- app/services/schedule_mlb.py (Phase 0)
- app/services/daily_board.py
- static/home.html, static/mlb.html, static/style.css
- app/main.py

OUT OF SCOPE
- Live scores / ticker (Phase B)
- Full model/markets on game page (Phase C)
- News (Phase D)
- Player props (Phase E)

ACCEPTANCE CRITERIA
BACKEND
- GET /api/schedule/mlb?date=YYYY-MM-DD — serve from data/processed/mlb_schedule_{date}.json if fresh (<6h), else fetch MLB Stats API and write cache
- GET /api/games/mlb/{game_id} — single game metadata + game_id match to daily board slate row when available
- Team logos via https://www.mlbstatic.com/team-logos/team-cap-on-dark/{teamId}.svg
- data/processed/mlb_teams.json — team name → team_id map, built on first schedule fetch

FRONTEND
- static/index.html (replace or supersede home.html) — sport pills (MLB active; NBA/NFL/NHL/CFB disabled "Coming soon"), ticker placeholder, news placeholder, "Last updated" from /api/status/refresh
- static/mlb_slate.html — game cards: away @ home, logos, local start time, status badge; tap → /mlb/game/{game_id}
- static/game.html + static/game.js — matchup header (logos, names, center time/date); empty 3-column markets box (Moneyline | Total | Spread); placeholders for Model / Parlays / Props
- static/app.css + static/app.js — shared layout, fetchJSON, time formatting, logo URL helper; mobile-first; extend dark theme from style.css

ROUTES (app/main.py)
- / → index.html home
- /mlb → mlb_slate.html (NOT old table board)
- /mlb/game/{game_id} → game.html
- /mlb/board → old mlb.html analytics table (link as "Advanced board")
- /mlb/lab unchanged

OTHER
- Opening /mlb after Phase 0 morning run shows today's games without Run live
- Logos and start times correct; game detail opens with correct matchup
- Mobile layout works; /mlb/board and /mlb/lab still work
- tests/test_schedule_mlb.py for schedule API + cache hit/miss

VERIFY
.\scripts\dev.ps1
Open /, /mlb, /mlb/game/{id}, /mlb/board, /mlb/lab
pytest -q

DO NOT
- Git commit unless user asks
- Implement live score polling (Phase B)
- Wire full insights endpoint (Phase C)

When done, report: routes added, how to test manually, screenshot description of home + slate.
```

**End of prompt.**

---

## Phase B — Live scores & ticker

**Start copying below this line.**

```
Implement Phase B from ROADMAP.md — Live scores & ticker.

PREREQUISITE
Phase A complete: home, mlb slate, game detail shell, schedule API.

GOAL
Sticky rolling ticker and live scores on game cards and game detail header. Fast polling separate from morning cache.

READ FIRST
- ROADMAP.md (Phase B)
- app/services/schedule_mlb.py
- static/index.html, static/mlb_slate.html, static/game.html, static/app.js

OUT OF SCOPE
- Model/markets/parlays on game page (Phase C)
- News, second sport (Phase D)
- Player props (Phase E)
- Changing morning_refresh schedule

ACCEPTANCE CRITERIA
BACKEND
- Extend schedule fetch with hydrate=linescore
- Normalize live fields: inning_half, inning, outs, period_label (e.g. "Bot 7th"), abstractGameState → scheduled | live | final
- GET /api/scores/today?sport=mlb — all today's games for ticker; in-memory or file cache TTL 30–60 seconds
- Morning cache (Phase 0) unchanged; live endpoint used during the day

FRONTEND
- Sticky horizontal ticker on home, /mlb, game pages; auto-refresh every 60s
- Ticker item: away @ home, score or start time, LIVE/Final badge, period_label when live
- Game cards on mlb_slate: LIVE badge, scores, inning/final label
- Game detail header: scores beside logos when live; center shows inning/status or start time pre-game
- No full page reload on poll — update DOM from /api/scores/today

OTHER
- Final games show final score (greyed or Final badge)
- Scheduled games show start time only
- tests for live status normalization and scores endpoint

VERIFY
pytest -q
Manual: during MLB season or mock linescore in tests; ticker renders on all three page types

DO NOT
- Git commit unless user asks
- Phase C insights endpoint
- Poll faster than 60s without documenting load

When done, report: cache TTL chosen, poll interval, how live vs final vs scheduled display.
```

**End of prompt.**

---

## Phase C — Model, markets & parlays on game page

**Start copying below this line.**

```
Implement Phase C from ROADMAP.md — Model, markets & parlays on game page.

PREREQUISITE
Phase B complete: live ticker and scores working.

GOAL
Game detail is the main product page: markets box + model recommendations + parlays for this game. Uses morning daily_board.json + live schedule.

READ FIRST
- ROADMAP.md (Phase C)
- app/services/daily_board.py (slate row shape, top_parlays)
- app/parlay/ev_ranker.py
- static/game.html, static/game.js
- DEV.md, MARKET.md

OUT OF SCOPE
- News (Phase D)
- Player props (Phase E)
- Spread model picks (display sportsbook run line only)
- Replacing /mlb/board entirely

ACCEPTANCE CRITERIA
BACKEND
- GET /api/games/mlb/{game_id}/insights — merge:
  - Live/schedule state (Phase B)
  - Moneyline + O/U from daily board slate row for game_id
  - Spread (run line) from The Odds API when ODDS_API_KEY set — display only
  - Model: model_prob_home, best_pick, expected_total_runs, edges, confidence_label
  - Parlays from top_parlays where any leg matches game_id (up to 3)
- Query ?refresh=true bypasses 5-min board TTL (delegate to build_daily_board refresh)
- Demo: ?date=2025-08-15&use_cache=true on insights endpoint
- Document spread/run line source in DEV.md

FRONTEND (game.html / game.js)
- Markets box: Moneyline | Total | Spread — American odds + implied % when available
- Model block: picked team, win %, estimated runs, edge vs market, confidence tier
- Parlay block: ranked parlays including this game; link "View all" → /mlb/board
- Disclaimers visible (reuse daily board copy)
- Empty states when odds or model missing

OTHER
- Pre-game: ML, O/U, spread from morning cache or "—"
- Model pick matches /api/daily for same game_id
- tests/test_game_insights.py with mocked board + schedule

VERIFY
pytest tests/test_game_insights.py -q
pytest -q
Manual: open game detail after morning_refresh; compare to /api/daily slate row

DO NOT
- Git commit unless user asks
- Invent new edge thresholds
- Phase D/E features

When done, report: insights JSON shape, demo URL, any Odds API markets added for run line.
```

**End of prompt.**

---

## Phase D — News & second sport

**Start copying below this line.**

```
Implement Phase D from ROADMAP.md — News & second sport.

PREREQUISITE
Phase C complete: game detail with model + markets + parlays.

GOAL
Home shows real sports headlines; ticker and nav support a second sport (choose NBA if no model yet — schedule-only is OK).

READ FIRST
- ROADMAP.md (Phase D)
- static/index.html, static/app.js
- app/services/schedule_mlb.py (pattern for second sport)

OUT OF SCOPE
- Player props (Phase E)
- Full NBA model/insights unless already exists in repo
- Writing original news content

ACCEPTANCE CRITERIA
NEWS
- app/services/news_feed.py — fetch RSS (document source in DEV.md; e.g. ESPN or league feed)
- GET /api/news — title, link, published, source; cache 15 min
- Home: 5–10 headline cards, links open new tab
- Graceful fallback if RSS down

SECOND SPORT (default: NBA schedule-only if no NBA model in repo)
- schedule service or parallel module for chosen sport (logos, start times, scores via Phase B pattern)
- Sport tab enabled in nav (no longer "Coming soon" for that sport)
- GET /api/scores/today supports sport=mlb,nba (or chosen sport)
- Ticker merges both sports sorted by start_time
- Game list page for second sport; game detail shows markets from Odds API if available; model block "Coming soon" if no model

MORNING JOB
- Extend morning_refresh.py with optional --sports mlb,nba when second sport ships (NBA board only if model exists; always refresh NBA schedule cache)

OTHER
- tests/test_news_feed.py with mocked RSS
- DEV.md: RSS source, ToS note

VERIFY
pytest -q
Manual: home headlines load; ticker shows MLB + sport #2

DO NOT
- Git commit unless user asks
- Phase E props
- Scrape sites that violate ToS

When done, report: RSS source chosen, second sport chosen, what's model-backed vs schedule-only.
```

**End of prompt.**

---

## Phase E — Player props

**Start copying below this line.**

```
Implement Phase E from ROADMAP.md — Player props.

PREREQUISITE
Phase D complete (or Phase C if skipping D temporarily — user must confirm).

GOAL
Game detail includes player props section: market lines from The Odds API first; model recommendations later (stub OK).

READ FIRST
- ROADMAP.md (Phase E)
- static/game.html, static/game.js
- app/services/daily_board.py (Odds API patterns)
- DEV.md

OUT OF SCOPE
- Prop prediction models / +EV prop picks (stub "Recommended — coming soon")
- Same-game parlay builder UI
- Morning refresh of props at 12:01 AM (optional midday only — document quota)

ACCEPTANCE CRITERIA
BACKEND
- app/services/props_mlb.py — fetch player props for a game from The Odds API
- GET /api/games/mlb/{game_id}/props — player, market_type (hits, HR, Ks, etc.), line, over_odds, under_odds
- Per-game cache with TTL documented in DEV.md
- Clear empty state: no key, no market, game too far out

FRONTEND
- Props section on game detail — table or cards, collapsible on mobile
- Tabs or sections: "Lines" (populated) | "Recommended" (empty state until model exists)
- Disclaimer on props section

OTHER
- Document Odds API quota impact in DEV.md
- tests/test_props_mlb.py with mocked API response

VERIFY
pytest tests/test_props_mlb.py -q
pytest -q
Manual: game with props available when ODDS_API_KEY set

DO NOT
- Git commit unless user asks
- Build prop ML model in this task

When done, report: markets supported, cache TTL, example prop row JSON.
```

**End of prompt.**

---

## Stress-test fixes — Odds API cache + daily board cache alignment

**Start copying below this line.**

```
Fix two issues found by scripts/stress_test_site.py load testing.

GOAL
Stop hammering The Odds API on every game insights page view, and make morning_refresh cache reusable by /api/daily and /mlb/board without rebuilding.

READ FIRST
- app/services/game_insights.py (_build_markets calls fetch_mlb_odds per request)
- app/odds/the_odds_api.py
- app/services/scores_mlb.py (SCORES_CACHE_TTL_SECONDS pattern)
- app/services/daily_board.py (cache_key with skip_totals)
- app/services/morning_refresh.py
- scripts/stress_test_site.py
- DEV.md

PROBLEM 1 — Insights fetches odds on every request
Each GET /api/games/mlb/{id}/insights calls fetch_mlb_odds(include_spreads=True) live.
Browsing N games = N API credits. Stress test caused 429/401 rate limits.

PROBLEM 2 — Morning cache key mismatch
morning_refresh uses skip_totals=False → cache_key contains "_totals_".
/api/daily default skip_totals=True → cache_key contains "_no_totals_".
Morning daily_board.json is ignored; /api/daily rebuilds (slow, extra API credit).

ACCEPTANCE CRITERIA

1) Odds API response cache
- Add module-level cache in app/odds/the_odds_api.py (or small helper) for fetch_mlb_odds results
- TTL: 300 seconds (5 min), configurable constant ODDS_CACHE_TTL_SECONDS
- Cache key: markets string (h2h,totals,spreads combo) — one entry per market set
- Concurrent requests during cache miss: only one HTTP call (simple lock or accept rare duplicate; prefer lock if easy)
- On 429/401/HTTP error: return stale cache if available; else None; log warning (do not crash insights)
- game_insights.py uses cached fetch — no behavior change to response JSON shape

2) Daily board cache alignment
- When /api/daily or build_daily_board is called with skip_totals=True (default live) and refresh=False:
  - If on-disk daily_board.json exists for same date with skip_totals=False (morning board) and age < 24h, serve it for read paths that only need slate/parlays (do NOT require exact cache_key match for this fallback)
  - OR simpler: change /api/daily live default to skip_totals=False to match morning_refresh (document in DEV.md)
  - Pick the simpler approach that avoids duplicate builds; document choice in DEV.md
- /mlb/board "Run live" should hit cached morning board without full rebuild when cache is fresh (<5 min TTL still applies for exact key match; morning fallback when keys differ)
- Do not change model thresholds or parlay logic

3) Tests
- test that fetch_mlb_odds returns cached result within TTL (mock httpx)
- test that insights for two games in same test only triggers one odds HTTP call
- test morning board (skip_totals=False) is returned by /api/daily without rebuild when skip_totals=True query default
- scripts/stress_test_site.py still passes (pytest tests/test_stress_site.py if present)
- Full non-slow pytest green

4) Docs
- DEV.md: odds cache TTL, morning vs daily cache behavior, quota note (1 credit per 5 min per market set, not per game page)

VERIFY
.\.venv\Scripts\Activate.ps1
pytest tests/ -q --ignore=tests/test_model_lab.py
.\.venv\Scripts\python.exe scripts\stress_test_site.py --sessions 8 --workers 6 --games 5

DO NOT
- Git commit unless user asks
- Phase D/E features
- Change 8% edge or model code

When done, report: approach chosen for cache alignment, TTL values, and stress test latency before/after.
```

**End of prompt.**

---

## UX polish — Background depth & visual polish

**Start copying below this line.**

```
Implement visual polish: richer backgrounds, glass header, game-page team wash, home hero stats, and light motion — without changing backend model/odds logic.

GOAL
Replace the flat black body with subtle depth (spotlight + grain), add sticky header blur, team-color ambient wash on game pages, home hero stat chips, section dividers, improved empty states, and gentle card entrance motion. Site should feel more alive while staying readable on mobile.

READ FIRST
- static/style.css (CSS variables: --bg, --surface, --accent, --positive)
- static/app.css (app-shell, topbar, ticker, game cards, home-hero, empty states)
- static/index.html, static/mlb_slate.html, static/game.html
- static/app.js (gameCardColorStyle, teamPrimaryColor, loadTeamColors, renderEmptyState, renderMatchupHeader)
- static/game.js
- static/mlb_team_colors.json
- app/services/home_summary.py + GET /api/home/today (fields for hero chips)
- GET /api/status/refresh, GET /api/scores/today?sport=all

OUT OF SCOPE
- Light/dark theme toggle
- Full-bleed stadium photos or heavy background images
- Animated/moving backgrounds (no parallax blobs that drift)
- New backend endpoints unless hero chips need one trivial field already on /api/home/today
- Player props, new sports, model changes
- Git commit unless user asks

LOCKED DECISIONS
- Global background: radial spotlight (#0f1419 base + faint center glow using --accent at ~6% opacity) + fine grain overlay at ~3–4% opacity
- Game pages only: diagonal corner wash using away/home team colors at ~5% opacity (reuse mlb_team_colors.json + existing gameCardColorStyle helpers)
- Sticky chrome (app-topbar + live-ticker): semi-transparent surface + backdrop-filter blur (~12px); solid fallback when blur unsupported
- Motion: respect prefers-reduced-motion — disable fades/slides when set
- Keep max-width 720px app-shell; backgrounds are full-viewport, content unchanged
- MLB slate may use a very faint stitch/diamond CSS pattern at ≤3% opacity; Board/Lab pages stay flatter (no sport pattern)

ACCEPTANCE CRITERIA

1) Global background (style.css + app.css)
- body (or a new .app-bg layer behind .app-shell) uses:
  - Base color var(--bg)
  - Radial gradient centered on viewport: soft blue glow (accent-based), darker vignette at edges
  - Subtle noise/grain via CSS (repeating SVG filter or tiny data-URI) — visible only on close inspection, not distracting
- Cards/ticker/text contrast unchanged; WCAG-friendly on primary text
- No horizontal scroll introduced on mobile

2) Sticky header glass
- .app-topbar and .live-ticker use background: color-mix or rgba from --surface with ~85% opacity
- backdrop-filter: blur(12px) and -webkit-backdrop-filter where supported
- @supports not (backdrop-filter: blur(1px)) fallback: solid var(--surface)
- Border-bottom on topbar/ticker still visible

3) Game-page team-color wash (game.html / game.js / app.css)
- body or .app-shell gets class e.g. game-page-bg when on game detail
- After matchup loads, apply CSS custom properties --game-away-color and --game-home-color from teamPrimaryColor / gameCardColorStyle
- Pseudo-elements or fixed layers: away color top-left, home color bottom-right, ~5% opacity, large blur
- Wash does NOT apply on home, /mlb slate, /mlb/board, /mlb/lab, /nba pages

4) Home hero stat chips (index.html + app.js + app.css)
- Below home-hero tagline, render a row of compact chips using data already fetched on home:
  - Game count (MLB + NBA from scores or summary)
  - Model leans / +EV count (from /api/home/today: plus_ev_singles, games_on_slate, etc.)
  - Last refresh relative time (from /api/status/refresh ran_at or formatRefreshStatus)
- Chips are pills: small, muted border, accent dot or icon optional
- Graceful fallback: "—" or hide chip when data missing
- No extra API round-trip if existing Promise.all can supply fields

5) Section dividers
- Between major home sections (Today at a glance, Best bets, Watched, News): thin horizontal rule using linear-gradient (transparent → border → transparent)
- Class e.g. .app-section-divider; reuse on mlb_slate between refresh line and game list if it fits

6) Empty states (app.js renderEmptyState + app.css)
- Replace plain text-only empty states for: no games today, scores unavailable, best bets empty, news empty
- Add simple inline SVG icons (calendar, scoreboard, chart, newspaper) — monochrome, muted, ~24px
- Keep existing copy; icon + message + optional action link unchanged in behavior

7) Card entrance motion (app.css + minimal app.js if needed)
- .game-card and .glance-card: on first paint, subtle fade + translateY(6px) → 0 over ~300ms
- Stagger optional (nth-child delay ≤50ms each) — cap total so long lists don't cascade forever
- prefers-reduced-motion: reduce: animation: none

8) Sport pill icons (optional if quick)
- Small inline SVG or emoji-free text marks next to MLB / NBA pills on pages that have sport-pills
- Disabled pills unchanged

9) Tests
- tests/test_pages.py: assert home still has today-glance, best-bets; game.html still loads
- If new body classes or critical CSS classes added, one assertion for game-page-bg class on game route HTML or document structure
- Full pytest -q green (no new slow integration tests)

10) Docs
- DEV.md: one short "Visual layer" note — background approach, reduced-motion, no new assets folder required unless grain SVG added under static/

APPROACH (suggested order)
1. style.css / app.css — body background layers + grain + section dividers
2. app.css — glass topbar/ticker
3. game.html body class + game.js set team color CSS vars after insights/matchup load
4. index.html + app.js — renderHomeHeroChips() fed from existing home bootstrap data
5. renderEmptyState icon map + CSS
6. Card animation CSS
7. Tests + DEV.md blurb

VERIFY
.\scripts\dev.ps1  (or uvicorn app.main:app)
Manual:
- / — see spotlight/grain, hero chips, section dividers, card fade-in
- /mlb — glass header over scrolling content; faint pattern optional
- /mlb/game/{id} — team-color corner wash matches away/home
- Resize mobile (~390px) — no overflow, text readable
- prefers-reduced-motion in DevTools — animations off
pytest tests/test_pages.py -q
pytest -q

DO NOT
- Git commit unless user asks
- Change ticker marquee logic, API contracts, or model thresholds
- Add large image assets or external font CDNs
- Put team-color wash on every page site-wide

When done, report: CSS approach for grain (pure CSS vs static file), sample hero chip row HTML, which pages got sport pattern, and before/after screenshot description.
```

**End of prompt.**
