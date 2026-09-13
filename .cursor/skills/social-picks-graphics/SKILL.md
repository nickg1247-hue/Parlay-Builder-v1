---
name: social-picks-graphics
description: Generate social media pick graphics in the NTG Sports / Pick Vault style — ladder challenges, free plays, best bets, win recaps, and moneyline parlays. Use when the user asks to create, design, or generate betting graphics, Instagram posts, pick cards, parlay slides, or cash/recap images matching their existing visual style.
---

# Social Picks Graphics — Style & Generation

Generate **1080×1350** (Instagram portrait) or **1080×1080** (square) sports betting graphics that match the user's reference set in `docs/social-graphics/`.

## When to use

- User asks for a pick graphic, parlay card, ladder day, free play, best bets, or win recap image
- User says "make it look like my other graphics" or references Pick Vault / NTG Sports social posts
- User provides today's picks and wants a shareable image

## Before generating

1. Read `docs/social-graphics/STYLE_GUIDE.md` for colors, typography, and effects
2. Read `docs/social-graphics/reference/README.md` to pick the correct **template type**
3. Gather from user or project data:
   - Player name, headshot (or team jersey color for placeholder)
   - Prop line (e.g. OVER 0.5 HITS, UNDER 15.5 STRIKEOUTS)
   - Odds (American format: -157, +122, +1589)
   - Matchup (team logos/abbreviations, game time)
   - Brand: **THE PICK VAULT** (green shield + lock) or **NTG SPORTS** (win recaps)
   - Day number for ladder challenges

## Template types

| Type | Use when | Reference |
|------|----------|-----------|
| **ladder-challenge** | Multi-day challenge, 2+ prop legs, day counter | `reference/README.md` §1 |
| **free-play** | 2-leg parlay giveaway, "+122" style | §2 |
| **best-bet-plays** | 3 singles, same stat type (hits O/U) | §3 |
| **win-recap** | Post-results, checkmarks, "CASHED" | §4 |
| **moneyline-parlay** | 3–5 team ML legs, combined odds | §5 |

## Generation prompt structure

When calling the image generation tool, build prompts in this order:

```
[FORMAT] Vertical sports betting social graphic, 1080x1350, Instagram post.

[STYLE] Dark charcoal/black grunge concrete texture background. Neon lime green (#A3FF00) accents with outer glow. White bold distressed sans-serif headlines. Brush-script green words for emphasis. Thin neon green borders on cards. Stadium light flares in corners. Premium aggressive sports media aesthetic.

[BRAND] Top-left: {THE PICK VAULT shield logo | NTG SPORTS wordmark}.

[LAYOUT] {template-specific layout from reference README}.

[CONTENT]
- Pick 1: {player}, {line}, {odds}, {matchup}, {time}
- Pick 2: ...
{parlay odds if applicable}

[FOOTER] Value-prop icons row + tagline "{tagline from template}".

[CONSTRAINTS] No blurry text. Legible player names and odds. Professional sports photography style for player cutouts. Team logos accurate. Do not invent fake stats — use only provided pick data.
```

## Brand rules

| Brand | When | Logo | Accent |
|-------|------|------|--------|
| **THE PICK VAULT** | Pre-game picks, ladders, free plays, ML parlays | Green shield + padlock + "THE PICK VAULT" | Lime green `#A3FF00` |
| **NTG SPORTS** | Win recaps, results, "we cashed" posts | "NTG" white + "SPORTS" green | Same lime green |

Site brand (`static/brand.css`) uses blue/cyan for the web app. **Social graphics use lime green** — do not swap to site cyan unless user asks.

## Typography hierarchy

1. **Hero** — Distressed bold caps (BEST BET, LADDER, FREE PLAY, WE CASHED)
2. **Emphasis** — Brush-script lime green (CHALLENGE, PLAY, AND WE CASHED!)
3. **Player last name** — Large brush-script green
4. **Bet line** — Bold white (OVER 0.5, UNDER 17.5 OUTS)
5. **Category** — Green caps (HITS O/U, RBIS O/U)
6. **Odds** — Large white numbers, right-aligned or in green paint-stroke box
7. **Body** — Clean sans-serif white, small caps for taglines

## Card anatomy (prop picks)

Each pick row/card should include:

- Left: circular player headshot on team-color background
- Center: name, bet line, stat category
- Right: odds (if single) OR matchup logos + "TODAY • H:MM PM"
- Bottom bar (optional): away @ home abbreviations + time

## Value-prop footer (reuse across templates)

Four icons in a green-bordered row — pick 4 from:

| Icon | Label | Subtext |
|------|-------|---------|
| Target | DATA BACKED / RESEARCHED | Stats, matchups, trends |
| Bar chart | HIGH PROBABILITY | Best spots, hit rate |
| Shield | DISCIPLINED APPROACH | No guessing, just process |
| Checkmark | CONSISTENT EDGE | Small edges, long-term |
| Trophy | PROVEN APPROACH | Track record |
| Money bag | BUILT FOR CONSISTENCY | Ladder / bankroll framing |

Standard taglines (rotate):

- "SMART PICKS. REAL EDGE. BET WITH CONFIDENCE."
- "DISCIPLINE. STRATEGY. RESULTS."
- "STAY DISCIPLINED. KEEP CLIMBING."
- "WE DON'T GUESS. WE ANALYZE. WE WIN."

## Win recap specifics

- Slanted green "PICK 1" / "PICK 2" corner banners
- Green "WINNER" label + large checkmark in glowing circle
- SGP rows: stacked players with individual checkmarks
- "CLEAN SWEEP!" brush-script callout
- Circular badge: "ANOTHER WINNING NIGHT!"

## Output checklist

- [ ] Correct template type and aspect ratio
- [ ] All pick data matches user input (no hallucinated lines/odds)
- [ ] Brand (Pick Vault vs NTG) matches post type
- [ ] Text is legible at phone scale
- [ ] Footer disclaimer tone: "TAIL RESPONSIBLY" on parlay posts when appropriate
- [ ] Filename: `{type}_{date}_{slug}.png` (e.g. `ladder_day2_2026-06-29.png`)

## Pulling picks from the project

When generating from live data:

- MLB props: `/api/props` or prop explorer ranked rows
- Daily board ML picks: `/api/daily` moneyline edges
- Parlay legs: parlay builder / slip optimizer output

Always confirm picks with the user before generating final graphics.

## Reference files

- Style tokens: `docs/social-graphics/STYLE_GUIDE.md`
- Template layouts: `docs/social-graphics/reference/README.md`
- User's original reference images: drop into `docs/social-graphics/reference/` as PNG/JPG
