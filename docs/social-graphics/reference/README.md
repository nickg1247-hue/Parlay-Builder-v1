# Reference Graphics Catalog

Catalog of the user's reference social graphics. Use these layouts when generating new images.

**Original image files:** Save your PNG/JPG copies in this folder (`docs/social-graphics/reference/`) with the filenames below so future sessions can load them as visual references.

| File (suggested) | Template ID | Brand |
|------------------|-------------|-------|
| `01-ladder-challenge-day2.png` | `ladder-challenge` | THE PICK VAULT |
| `02-free-play-2pick-parlay.png` | `free-play` | THE PICK VAULT |
| `03-best-bet-plays-3singles.png` | `best-bet-plays` | THE PICK VAULT |
| `04-win-recap-clean-sweep.png` | `win-recap` | NTG SPORTS |
| `05-moneyline-parlay-4team.png` | `moneyline-parlay` | THE PICK VAULT |

---

## 1. Ladder Challenge (`ladder-challenge`)

**Reference:** Day 2 ladder with 2 prop legs + parlay odds sidebar.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [PICK VAULT logo]              DAY 2 [ladder icon]        │
│                                                             │
│  LADDER          ONE LADDER. ONE GOAL.                      │
│  CHALLENGE       BUILDING TODAY. CASHING TOMORROW.          │
│                  KEEP THE MOMENTUM...                       │
│                                                             │
│  ┌─ Prop Card 1 ─────────────────────────────────────┐     │
│  │ [photo] UNDER 0.5          -289                    │     │
│  │         TYLER O'NEILL RBIS O/U                     │     │
│  │         [WSH][BAL]  TODAY 7:05 PM                  │     │
│  └────────────────────────────────────────────────────┘     │
│  ┌─ Prop Card 2 ─────────────────────────────────────┐     │
│  │ [photo] UNDER 15.5         -157                    │     │
│  │         JACK PERKINS STRIKEOUTS O/U                │     │
│  │         [OAK][LAA]  TODAY 7:05 PM                  │     │
│  └────────────────────────────────────────────────────┘     │
│                                    ┌─────────────────┐      │
│                                    │ TODAY'S PARLAY  │      │
│                                    │ ODDS    +443    │      │
│                                    └─────────────────┘      │
│                                    [4 value props]          │
│  [4 icon features row]                                      │
│  STAY DISCIPLINED. KEEP CLIMBING.                           │
│  [logo] SMART PICKS | REAL EDGE | REAL RESULTS              │
└─────────────────────────────────────────────────────────────┘
```

### Key elements

- **Day counter** top-right: large brush "DAY N" + small motivational subtext
- **Two stacked prop cards** with green glowing borders
- **Parlay odds box** right column (paint-stroke green background)
- **Sidebar value props:** Carefully Selected, Built for Consistency, etc.

### Variables

`day_number`, `prop_legs[]` (player, line, stat, odds, teams, time), `parlay_odds`

---

## 2. Free Play (`free-play`)

**Reference:** 2-pick hits parlay giveaway.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [logo]                         OUR FREE PICK OF THE DAY     │
│                                                             │
│  FREE                                                       │
│  PLAY              TWO PROPS. ONE EDGE.                     │
│                                                             │
│  ═══════════ 2 PICK PARLAY  >>>  +122 ═══════════          │
│                                                             │
│  ┌─ Player 1 ────────────────────────────────────────┐     │
│  │ [photo/T bg]  WYATT          [TEX][TOR]             │     │
│  │               LANGFORD       TODAY 3:07 PM          │     │
│  │               [1+ HITS pill]                        │     │
│  └────────────────────────────────────────────────────┘     │
│  ┌─ Player 2 ────────────────────────────────────────┐     │
│  │ [photo/P bg]  TREA           [PHI][NYM]             │     │
│  │               TURNER         TODAY 4:10 PM          │     │
│  │               [1+ HITS pill]                        │     │
│  └────────────────────────────────────────────────────┘     │
│                                                             │
│  [4-col value grid: Data Backed | High Prob | Discipline | Edge] │
│  SMART PICKS. REAL EDGE. BET WITH CONFIDENCE.               │
│  THE PICK VAULT | DISCIPLINE. | STRATEGY. | RESULTS.      │
└─────────────────────────────────────────────────────────────┘
```

### Key elements

- **"FREE PLAY"** split white/green headline
- **Green parlay odds bar** across center-top
- **Green pill button** for simplified line (1+ HITS vs O/U format)
- **Team-colored photo backgrounds** (faded team letter behind headshot)

### Variables

`legs[]` (2 players), `parlay_odds`, `stat_pill_text` (e.g. "1+ HITS")

---

## 3. Best Bet Plays (`best-bet-plays`)

**Reference:** 3 single-hit overs, equal rows.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [logo]                              TODAY'S               │
│                                     • High Probability      │
│  BEST BET                           • Strong Matchups       │
│  PLAYS                              • Single Bets           │
│  DATA. TRENDS. VALUE. THAT'S THE EDGE.                      │
│                                                             │
│  ─── Row 1: [action photo] LARS NOOTBAAR | OVER 0.5 | MIA@STL ───
│  ─── Row 2: [action photo] WYATT LANGFORD | OVER 0.5 | TEX@TOR ───
│  ─── Row 3: [action photo] HENRY BOLTE  | OVER 0.5 | OAK@LAA ───
│                                                             │
│  [4 value icons footer]                                     │
│  SMART PICKS. REAL EDGE. BET WITH CONFIDENCE.               │
└─────────────────────────────────────────────────────────────┘
```

### Key elements

- **Action photos** (in-game swings) instead of headshots
- **Three equal horizontal bands** separated by green lines
- **Right sidebar callout** "TODAY'S" with bullet value props
- Same stat type across all rows (visual consistency)

### Variables

`picks[]` (3+ singles: player, line, category, matchup, time)

---

## 4. Win Recap (`win-recap`)

**Reference:** 2-pick cash with SGP leg, NTG branding.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│                    [NTG SPORTS logo]                        │
│         WE CHASED. AND WE CASHED!                           │
│    OUR PLAN | OUR PROCESS | YOUR W                          │
│                                                             │
│  ┌─ PICK 1 ──────────────────────────────────────────┐     │
│  │ [banner]  [photo] WINNER  JACOB MISIOROWSKI  (✓)  │     │
│  │                    OVER 17.5 OUTS                  │     │
│  │           [CHC]  7:45 PM  [MIL]                    │     │
│  └────────────────────────────────────────────────────┘     │
│  ┌─ PICK 2 ──────────────────────────────────────────┐     │
│  │ WINNER 2 PICK SGP                                  │     │
│  │ [photo] BILLY COOK    UNDER 0.5 RBIS          (✓)  │     │
│  │ [photo] PETEY HALPIN  UNDER 0.5 RBIS          (✓)  │     │
│  │ [photo] VICTOR ROBLES UNDER 0.5 RBIS          (✓)  │     │
│  │           [SEA]  7:10 PM  [CLE]                    │     │
│  └────────────────────────────────────────────────────┘     │
│                                                             │
│  🏆 CLEAN SWEEP!                                            │
│  WE DON'T GUESS. WE ANALYZE. WE WIN.    [WINNING NIGHT badge]│
│  WE STAY LOCKED IN... | MORE WINNERS COMING...             │
└─────────────────────────────────────────────────────────────┘
```

### Key elements

- **NTG SPORTS** branding (not Pick Vault)
- **Slanted green corner banners** "PICK 1", "PICK 2"
- **Glowing green checkmark circles** on right
- **SGP block** stacks multiple players in one card
- **"CLEAN SWEEP!"** trophy callout

### Variables

`results[]` (pick label, player(s), line, outcome, matchup, time), `sweep=true/false`

---

## 5. Moneyline Parlay (`moneyline-parlay`)

**Reference:** 4-team ML parlay, +1589.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ [logo]                    TUESDAY JUNE 24 [green brush]     │
│                                                             │
│  4 TEAM                                                     │
│  MONEYLINE PARLAY              ┌──────────────┐           │
│  FOUR SOLID PICKS. ONE BIG EDGE│ PARLAY ODDS  │           │
│                                │   +1589      │           │
│  ┌─ ARI Diamondbacks ───────── vs TB | 7:10 PM ─┐         │
│  ┌─ MIA Marlins ────────────── vs ?? | time ─────┐         │
│  ┌─ LA Dodgers ─────────────── vs ?? | time ─────┐         │
│  ┌─ NYY Yankees ───────────── vs ?? | time ─────┐         │
│                                                             │
│  [right sidebar: 4 value props with icons]                  │
│                                                             │
│  WHY WE LIKE THIS PARLAY          STACKED. BACKED.          │
│  [checklist rationale]            BUILT TO HIT.             │
│                                   TAIL RESPONSIBLY.         │
│  [logo] DISCIPLINE. STRATEGY. RESULTS.                      │
└─────────────────────────────────────────────────────────────┘
```

### Key elements

- **Date brush-stroke** top-right
- **Team logo rows** with color-matched glow behind logo
- **"MONEYLINE"** green italic under team name
- **"WHY WE LIKE THIS PARLAY"** checklist bottom-left
- **Large CTA** bottom-right: STACKED. BACKED. BUILT TO HIT.

### Variables

`date_label`, `teams[]` (name, opponent, time), `parlay_odds`, `rationale_bullets[]` (4)

---

## Quick template picker

| User says… | Template |
|------------|----------|
| "ladder day 3" | `ladder-challenge` |
| "free play" / "POTD" | `free-play` |
| "3 best bets" / "singles card" | `best-bet-plays` |
| "we hit" / "recap" / "cashed" | `win-recap` |
| "ML parlay" / "4-team parlay" | `moneyline-parlay` |
