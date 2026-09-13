# Social Picks Graphics — Style Guide

Design system extracted from the user's reference graphics (Pick Vault + NTG Sports social posts). Use this when generating new pick images.

---

## Canvas

| Format | Size | Use |
|--------|------|-----|
| Portrait (default) | **1080 × 1350** | Instagram feed, Stories-safe |
| Square | **1080 × 1080** | X/Twitter, square feed |
| Landscape | **1920 × 1080** | YouTube thumb, Discord banner (rare) |

Safe zone: keep logos and key text **60px** from edges.

---

## Color palette

### Primary (social graphics)

| Token | Hex | Usage |
|-------|-----|-------|
| **Background** | `#0a0a0a` – `#1a1a1a` | Charcoal/black grunge base |
| **Lime accent** | `#A3FF00` / `#39FF14` | Headlines, borders, glows, icons |
| **White** | `#FFFFFF` | Primary text, player first names, odds |
| **Muted** | `#9CA3AF` | Matchup subtext, times |
| **Black on green** | `#000000` | Text inside green paint-stroke boxes |

### Secondary

| Token | Usage |
|-------|-------|
| Team jersey colors | Player photo backdrop glow (teal MIA, navy NYY, red PHI, etc.) |
| Orange/yellow sparks | Stadium atmosphere particles (sparse) |
| Dark blue vignette | Corners, depth |

> **Note:** The NTG Sports **website** uses blue/cyan (`#2563eb`, `#38bdf8`). Social pick graphics use **lime green** — keep them distinct unless rebranding.

---

## Background treatment

Layer stack (bottom → top):

1. **Base texture** — dark concrete, asphalt, or brushed metal grain
2. **Stadium photo** — blurred, desaturated, low opacity (~15–25%)
3. **Light flares** — green stadium lights top-left/right; warm orange bokeh optional
4. **Spray/splatter** — lime green paint strokes in corners (brush PNG style)
5. **Vignette** — darken edges

---

## Typography

### Font roles (describe in prompts; no exact font files required)

| Role | Style | Example |
|------|-------|---------|
| **Distressed headline** | Heavy bold sans, worn/stamped texture | `BEST BET`, `4 TEAM`, `LADDER` |
| **Brush emphasis** | Hand-painted script, lime green | `PLAY`, `CHALLENGE`, `CASHED!` |
| **Player name** | First: white bold caps; Last: green brush | `WYATT` / `LANGFORD` |
| **Bet line** | Extra-bold white | `OVER 0.5`, `UNDER 17.5 OUTS` |
| **Category** | Green caps, smaller | `HITS O/U`, `RBIS O/U` |
| **Odds** | Large bold white or green in box | `-289`, `+443`, `+1589` |
| **Tagline** | Small caps, letter-spaced | `DISCIPLINE. STRATEGY. RESULTS.` |
| **Body** | Clean sans regular | Value-prop descriptions |

### Size hierarchy (relative)

```
Hero title:     100%
Player last:     70%
Bet line:        65%
Odds:            60%
Category:        35%
Footer body:     25%
```

---

## Effects

| Effect | Spec |
|--------|------|
| **Neon glow** | Outer glow on green text/borders: `#A3FF00` at 40–60% opacity, 8–16px blur |
| **Card border** | 2px solid lime green, optional inner glow |
| **Paint stroke box** | Irregular green brush rectangle behind odds/date |
| **Checkmark badge** | Filled green circle, white ✓, glow ring |
| **Slanted banner** | Parallelogram corner tag (PICK 1, DAY 2) |
| **Player cutout** | Clean PNG headshot, soft drop shadow, team-color radial behind |

---

## Logo lockups

### THE PICK VAULT

- Green shield with padlock (and sometimes crossed keys)
- Wordmark: "THE PICK VAULT" — "THE" small, "PICK VAULT" bold
- Position: top-left, ~80px tall

### NTG SPORTS

- "NTG" large white bold
- "SPORTS" smaller, green, letter-spaced below
- Used on **win/recap** graphics primarily

---

## Component library

### Prop pick card (horizontal)

```
┌─────────────────────────────────────────────────────┐
│ [headshot]  FIRST                     odds (-289)   │
│             LAST (green brush)                        │
│             OVER 0.5                                  │
│             HITS O/U (green)                        │
│ ─────────────────────────────────────────────────── │
│  [away logo]  AWY @ HOM  [home logo]   TODAY 7:05PM │
└─────────────────────────────────────────────────────┘
```

### Parlay odds box

```
┌──────────────────┐
│ TODAY'S PARLAY   │
│     ODDS         │
│    +443          │  ← large, green glow
└──────────────────┘
```

### ML team row

```
┌────────────────────────────────────────┐
│ [team logo]  TEAM NAME                 │
│              MONEYLINE (green italic)  │
│              vs OPPONENT | 7:10 PM     │
└────────────────────────────────────────┘
```

### Value-prop grid (4 columns)

Each cell: green icon (48px) + bold white label + 1-line gray description.

---

## Content patterns by post type

| Post type | Title pattern | Key visual |
|-----------|---------------|------------|
| Ladder | `LADDER` + `CHALLENGE` + `DAY N` | Day counter top-right, 2 prop cards |
| Free play | `FREE` + `PLAY` + odds bar | 2 player rows, "+122" parlay bar |
| Best bets | `BEST BET` + `PLAYS` | 3 equal horizontal rows, action photos |
| Win recap | `WE CHASED.` + `AND WE CASHED!` | Checkmarks, WINNER labels |
| ML parlay | `N TEAM` + `MONEYLINE PARLAY` | Team logo rows, combined odds |

---

## Copy bank

### Headlines

- ONE LADDER. ONE GOAL. BUILDING TODAY. CASHING TOMORROW.
- TWO PROPS. ONE EDGE.
- DATA. TRENDS. VALUE. THAT'S THE EDGE.
- FOUR SOLID PICKS. ONE BIG EDGE.
- STACKED. BACKED. BUILT TO HIT.

### Closers

- SMART PICKS | REAL EDGE | REAL RESULTS
- LET'S GET IT. TAIL RESPONSIBLY.
- MORE WINNERS COMING. STAY LOCKED IN.
- OUR FREE PICK OF THE DAY

### Responsible gaming

Include on parlay/ladder posts when appropriate: "TAIL RESPONSIBLY" or "NOT BETTING ADVICE" in small footer text.

---

## Do / Don't

| Do | Don't |
|----|-------|
| High contrast, legible at phone size | Tiny odds or crowded text |
| Consistent green glow on accents | Flat green with no depth |
| Real team logos and player photos | Generic silhouettes |
| Match reference template layout | Invent new layout per post |
| Use exact pick data from user/API | Hallucinate lines or odds |
| Grunge texture + stadium atmosphere | Clean flat corporate look |
