# Roadmap — 2026 Premier Padel Season (Women)

Goal: extend the existing single-tournament tool (Buenos Aires P1) to cover
every 2026 Premier Padel tournament, flag partner changes, and polish the visuals.

---

## Rules for Claude Code (read first)

1. Work on **one phase at a time**. Do not start the next phase until the user approves.
2. At the end of each phase: stop, show a short summary + how to verify, then wait.
3. Do **not** add features, refactors, or design changes that are not in the current phase.
4. Do **not** change any visual design before Phase 4.
5. Keep the existing behaviour working: Buenos Aires P1 output must look identical after Phases 1–3.
6. Women's matches only (unchanged).
7. At the end of each phase, update `CLAUDE.md` and `README.md` briefly (only what changed).

---

## Phase 0 — Tournament discovery script

New standalone script: `discover_tournaments.py`. Reusable every season.

```bash
python discover_tournaments.py --year 2026
```

### Confirmed findings (from manual DevTools check)

| Item | Where | Notes |
|---|---|---|
| Calendar | `https://www.padelfip.com/calendar-premier-padel/?events-year={year}` | Lists past AND upcoming events. Event links appear multiple times → deduplicate by URL |
| Premier Padel filter | Event element class `category-event-fip-ppt-*` (e.g. `category-event-fip-ppt-p1`) | Keep only these; tier = suffix (p1, p2, major, finals) |
| Tournament ID | Event element class `idEvent_{id}` | ✅ Confirmed equal to widget ID. Buenos Aires: `idEvent_2209`, Riyadh: `idEvent_902` |
| OOP endpoint | Event page source → JS variable `fetchUrl: "/wp-content/themes/padelfiptheme/template-parts/event/endpoint/get-oop-data.php?year=2026&id=2209&day=8&totalday=8&widget=oopbyday"` | Also gives `totalday` directly. Use it to cross-check the computed date range |
| Dates | Event page → `p.overview__text` under `span.overview__title` "Qualification" and "Main draw" | Example: `Sunday 10 May – Tuesday 12 May 2026` / `Tuesday 12 May–Sunday 17 May 2026`. start = Qualification start, end = Main draw end. Year appears only at the end; dash may be `–` or `-` with or without spaces. If no Qualification block, start = Main draw start |
| Entry List (women) | `{event_url}?tab=Player+List` → Female toggle → "Entry List Female" link | ⚠️ NOT in static page source — loaded by JavaScript. See investigation step below. Filenames are inconsistent (`Entry-list-BUENOS-AIRES-P1-W-v8.pdf`, `Entry-list-Women-RIYADH-SEASON-P1-v5-1.pdf`) → select by **link text / label**, never by filename |

### Investigation step (do this first, before writing the script)

Find how the Entry List links are loaded. Known examples to verify against:
- Buenos Aires → `https://www.padelfip.com/wp-content/uploads/2025/12/Entry-list-BUENOS-AIRES-P1-W-v8.pdf`
- Riyadh → `https://www.padelfip.com/wp-content/uploads/2025/12/Entry-list-Women-RIYADH-SEASON-P1-v5-1.pdf`

Where to look, in this order:
1. Event page source: other endpoint URLs under `/wp-content/themes/padelfiptheme/template-parts/event/endpoint/` (same folder as `get-oop-data.php`)
2. Event page source: `admin-ajax.php` calls and their `action` parameter (this request was seen in the Network tab)
3. Inline JS / JSON blobs that may contain the PDF URL

Rules:
- `requests` only. Do not use Playwright unless all 3 fail — then ask the user first.
- Report the finding (endpoint + params + sample response) and **stop for approval** before writing the script.

### Logic
1. Fetch the calendar page → collect unique event URLs with class `category-event-fip-ppt-*`
2. For each event page: read tournament ID, tier, name, start/end dates (total_days = end − start + 1)
3. Fetch `?tab=Player+List` → find "Entry List Female" PDF link
4. Download each PDF to `data/pdfs/<slug>/entry_list_women.pdf`
   (local copy protects against PDFs being removed later)
5. Write `data/tournaments.json` with a `status` field: finished / ongoing / upcoming (based on today)
6. Print a summary table: slug, tier, ID, dates, status, PDF found yes/no
7. Test mode: `--only buenos-aires-p1-2026` processes a single tournament

### Rules
- `requests + BeautifulSoup` only. No Playwright.
- 1 second delay between requests.
- Missing field → write `null` and log a warning, never crash.
- Re-running merges with the existing JSON; never overwrite manually edited fields.

### Output: `data/tournaments.json`

```json
[
  {
    "slug": "buenos-aires-p1-2026",
    "name": "Premier Padel Buenos Aires P1 2026",
    "tier": "P1",
    "tournament_id": 2209,
    "year": 2026,
    "start_date": "2026-05-10",
    "total_days": 8,
    "entry_list_pdf_url": "https://www.padelfip.com/wp-content/uploads/..."
  }
]
```

---

## Phase 1 — Config-driven refactor (no new features)

Remove hardcoded tournament constants from `scrape_matches.py` and `main.py`.
Read them from `data/tournaments.json` instead.

- Add CLI arg: `python main.py --tournament buenos-aires-p1-2026`
- Default: the tournament whose dates include today, else the most recent one
- Rankings cache becomes per tournament: `data/cache/<slug>/entry_list.json`

**Done when:** Buenos Aires output is identical to before, and running with a
second tournament slug from the JSON also works.

---

## Phase 2 — All tournaments

- New command: `python main.py --all` → generates every tournament in `tournaments.json`
- Output structure:
  ```
  output/
  ├── index.html              # landing page: list of tournaments (name, dates, tier, status)
  └── <slug>/index.html       # existing per-tournament page
  ```
- Each tournament page gets a small "← All tournaments" link
- **Caching:** finished tournaments are fetched once and frozen in
  `data/cache/<slug>/` — never re-fetched. Only an ongoing tournament is refreshed
  in `--watch` mode.
- Upcoming tournaments: show on landing page as "Upcoming", no page generated
  until the schedule exists.

**Done when:** landing page lists all tournaments, each finished tournament has
a working page, and a second `--all` run makes zero HTTP requests for finished ones.

---

## Phase 3 — Partner change tracking

Data source: the Entry List PDFs (they already list pairs).

1. Build `data/partnerships.json`: for every tournament (ordered by start date),
   store each pair: `{player_slug, partner_slug, tournament_slug}`
2. For each pair in a tournament, compare each player's partner with her partner
   in the **most recent previous tournament she played** (not just the previous
   tournament on the calendar).
3. If different → mark the pair as a new partnership.

Display (minimal, no redesign yet):
- Small `NEW PAIR` badge next to the team on the match card
- Hover tooltip: `Previously with M. Calvo (Buenos Aires P1)`
- First tournament of the season: no badge (nothing to compare against)

**Done when:** at least one known partner change shows the badge with the correct
previous partner and tournament.

---

## Phase 4 — Visual polish

Starts only after the user provides a reference (screenshot or description).
Scope is CSS and HTML templating only: landing page, header, date nav, badges.
Match cards stay based on the widget's HTML unless the user explicitly says otherwise.
No changes to scraping, caching, or matching logic in this phase.

---

## Known risks

| Risk | Mitigation |
|---|---|
| Old Entry List PDFs removed from padelfip.com | Cache PDFs locally once found; if missing, log it and skip rank badges for that tournament |
| PDF layout differs between tournaments | Use `_debug_pdf_rows()` per tournament; adjust parser, don't rewrite it |
| Widget data unavailable for old tournaments | Log and show "No data" on landing page instead of crashing |
| Name spelling differs across PDFs | Match on PDF slug, reuse existing `_make_lookup_index()` |
