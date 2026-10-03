# Design Roadmap — FIP Padel Viewer

Goal: give the project a modern, clean look. Visual changes only.

Inspiration: modern padel score sites (photo-based tournament cards, hover effects,
clean typography). Use them as **inspiration only — do not copy any site's CSS,
assets, or layout code.**

---

## Rules for Claude Code (read first)

1. **Design only.** Do not change scraping, caching, name matching, partner tracking,
   or any data logic. Python changes are limited to HTML templating and CSS output.
2. **One phase at a time.** Stop after each phase, show a screenshot-ready summary,
   wait for approval.
3. **Do not rewrite the widget's match-card HTML.** Restyle it only through CSS overrides
   (see Phase D4).
4. All colors, fonts, and spacing come from the design tokens in Phase D1.
   No hardcoded colors anywhere else.
5. No new features. Out of scope: Results / Tableau view, custom ranking page,
   new filters, new data fields (one exception: Phase D2 image field).
6. Plain HTML + CSS + minimal vanilla JS. No frameworks, no build step.

---

## Phase D0 — Prototype (user, in Claude Design — not Claude Code)

The user designs and approves the look before any code changes:
- Landing page: tournament card grid
- One match card
- Header + date navigation

Output: approved reference HTML/CSS saved to `design/reference/`.
Claude Code uses this as the visual source of truth in all later phases.

---

## Phase D1 — Design tokens

Create `output/assets/theme.css` with CSS variables only:

Palette (dark theme): https://coolors.co/palette/10002b-240046-3c096c-5a189a-7b2cbf-9d4edd-c77dff-e0aaff

```css
:root {
  /* colors */
  --color-bg:           #10002B;  /* page background */
  --color-surface:      #240046;  /* cards */
  --color-surface-2:    #3C096C;  /* card header rows, hover state */
  --color-border:       #5A189A;  /* borders, dividers */
  --color-accent-hover: #7B2CBF;  /* button hover */
  --color-accent:       #9D4EDD;  /* primary accent: active date, buttons */
  --color-accent-2:     #C77DFF;  /* FIP rank badge, links */
  --color-highlight:    #E0AAFF;  /* winner emphasis, LIVE indicator */
  --color-text:         #FFFFFF;  /* body text (readability) */
  --color-text-muted:   rgba(224, 170, 255, 0.65);  /* secondary text: times, seeds */
  /* typography */
  --font-heading: ;
  --font-body: ;
  /* spacing & shape */
  --radius-card: ;
  --shadow-card: ;
  --shadow-card-hover: ;
  --space-1: ; --space-2: ; --space-3: ; --space-4: ;
}
```

Values are taken from `design/reference/`.
Generated pages link `theme.css` instead of writing colors inline.

**Done when:** changing one variable (e.g. `--color-accent`) updates the whole site.

---

## Phase D2 — Landing page (tournament cards)

- Responsive grid of tournament cards (`minmax(240px, 1fr)`, ~4 per row on desktop)
- Tournament images are **vertical posters** → cards are portrait:
  image area `aspect-ratio: 4 / 5`, `object-fit: cover`, `object-position: top`
  (keeps the logo/title at the top of the poster visible)
- Below the image: name, tier label (P1 / P2 / Major / Finals), dates,
  status (Finished / Live / Upcoming)
- Hover: card lifts slightly (`transform: translateY`), purple glow shadow, poster zooms a little
- Live tournament: visible "LIVE" indicator
- Whole card is clickable → tournament page

**Photo source (only allowed data change):**
add an `image_url` field to `data/tournaments.json`, read from the event page's
`og:image` meta tag in `discover_tournaments.py`.
If missing → fallback: same 4:5 box in `--color-surface-2` with a simple court-line drawing.
Note: `og:image` may be a landscape crop of the poster. If so, look for the poster
image itself on the event page and store that instead (log which one was used).

**Done when:** all tournaments render as cards, hover works, missing images fall back cleanly.

---

## Phase D3 — Tournament page header + date navigation

- Header: **no full-width banner** (vertical posters crop badly).
  Compact row instead: poster thumbnail on the left (~136 px wide, 4:5, rounded),
  then "← All tournaments", tournament name, tier label, dates · city on the right.
  Wraps under the poster on mobile.
- FIP rank badge may wrap to the next line on long names — never truncate player names
- Date nav: pill-style day buttons, clear active state, horizontal scroll on mobile
- Keep the existing fix: `.fip-date-nav` must never shrink (`flex-shrink: 0`)
- Local time shows the venue's time zone: `Local time 9:14 AM · ART (UTC−3)`
  - Add a `timezone` field (IANA name, e.g. `America/Argentina/Buenos_Aires`) to
    `data/tournaments.json` (second allowed data change)
  - The live JS clock uses `Intl.DateTimeFormat` with that time zone instead of the
    hardcoded UTC−3, so the abbreviation and offset are always correct (incl. DST)
- UTC offset appears **only** in the court header's Local time line. Match cards
  keep the widget's time note as is (`Starting at 9:00 AM`, `Followed by`) — no offset.
- Match card header shows only the round (`Quarterfinals`). Drop the "Women" label —
  the whole site is women-only.

**Done when:** header and nav match the reference; nav stays readable on every day.

---

## Phase D4 — Match cards (CSS overrides only)

Create `output/assets/overrides.css`, loaded **after** the widget's CSS.
Scope every rule under a wrapper class (e.g. `.fip-theme`) so nothing leaks.

Restyle:
- Card container (`table.w-100`): radius, shadow, spacing
- Header row (`tr.scorebox-header-*`): time note + category
- Player rows: font, winner emphasis
- Set scores (`td.set`, `.set-completed`, `.set-lost`)
- Summary row (`tr.summary`): duration, status

Layout rules:
- Cards in the same row have equal height
- Summary row (duration · status · Match stats) is always pinned to the bottom of the card
  (card = flex column, summary `margin-top: auto`)

Do not add, remove, or reorder widget HTML elements.

**Done when:** cards match the reference and badges/scores still render correctly.

---

## Phase D5 — Badges

Restyle with tokens, keep behavior unchanged:
- `FIP #N` rank badge (still a clickable link to the FIP profile)
- `NEW PAIR` badge: soft purple pill + link icon. On hover/focus a small card opens below it:
  "New partnership" / "Previously with {partner}" / "at {previous tournament}".
  Use a real `<button>` so it also opens with the keyboard (Tab).

Emoji rules:
- Flags: flag emoji per player (🇪🇸 🇦🇷 🇵🇹 …), mapped from the 3-letter country code
- 🏅 next to the match-winning pair, on every completed match
- 👑 next to the tournament-winning pair, **on the final match card only**
- Load **Noto Color Emoji** from Google Fonts and apply it to all emoji.
  Windows browsers do not render flag emoji natively (they show "ES", "AR" instead).

**Done when:** both badges are visually consistent with the new theme.

---

## Phase D6 — Polish

- Mobile layout check (≤ 400 px): cards stack, nothing overflows
- Optional dark mode via `prefers-color-scheme` (tokens only)
- Smooth transitions (≤ 200 ms), no layout jumps
- Update `README.md` with 2–3 new screenshots

---

## Out of scope (future feature roadmap)

- Results / Tableau (draw bracket) view
- Custom FIP ranking page
