# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Recommended: HTTP server + watch loop + open browser
python main.py --serve --watch --open

# Watch loop only (file:// — browser may cache aggressively)
python main.py --watch --open

# Single run — generate once and open
python main.py --open

# Force re-download of entry list PDF (bypass 24 h cache)
python main.py --force-refresh --open

# Pick a tournament by slug (default: the one being played today, else the most recent)
python main.py --tournament buenos-aires-p1-2026 --open

# Every tournament + landing page (finished ones come from the frozen cache)
python main.py --all --serve --watch --open

# Debug PDF parsing when a new tournament's PDF layout differs
python -c "from scrape_rankings import _debug_pdf_rows; from tournaments import get_tournament; _debug_pdf_rows(get_tournament('buenos-aires-p1-2026').entry_list_pdf_url)"

# Discover all Premier Padel tournaments of a season → data/tournaments.json + data/pdfs/
python discover_tournaments.py --year 2026
python discover_tournaments.py --year 2026 --only buenos-aires-p1-2026   # single tournament
python discover_tournaments.py --year 2026 --overwrite                   # scraped values replace existing ones

# Rebuild data/partnerships.json from the entry list PDFs and list every partner change
python partnerships.py

# Windows encoding fix if terminal shows garbled characters
set PYTHONUTF8=1 && python main.py --serve --watch --open
```

## Architecture

The tool is a **5-step pipeline** (matches, rankings, match stats, badge injection, HTML) — each step is a separate module, orchestrated by `main.py`.

### Data flow

```
matchscorerlive.com widget  →  scrape_matches.py  →  bodies_by_day (raw HTML fragments)
padelfip.com entry list PDF →  scrape_rankings.py →  rankings cache + lookup index
                                                            ↓
                                          generate_html.py (inject_rank_badges)
                                                            ↓
                                             output/index.html
```

### Key design decisions

**Widget HTML pass-through**: The output page does **not** re-render match cards. It keeps the widget's own HTML verbatim (CSS included via `<link>` tags copied from the widget's `<head>`). Only the badges, emoji, page header and date-nav bar are custom elements; the cards' look is changed by CSS overrides only (see "Design layer"). This means widget structure changes will break parsing without breaking visual design.

**Gender filter operates on HTML, not parsed data**: `filter_gender_html()` removes `<table class="w-100">` blocks where the `<b>` tag ≠ "Women". The structured `parse_widget()` function (used only for match counts/logging) is separate.

**BeautifulSoup fragment handling**: Both `filter_gender_html` and `inject_rank_badges` return `body_el.decode_contents()` instead of `str(soup)`. Using `str(soup)` causes `html.parser` to wrap fragments in `<html><body>` tags that then get embedded inside day-panel `<div>`s, producing invalid nested documents.

**Inline `<style>` stripping**: Widget body HTML contains `<style>` tags that would pollute the output page's CSS globally. `extract_widget_body()` strips all of them (both direct children of `<body>` and any nested inside the court row). All needed card styling is in the linked `.css` files.

**Sticky footer / nav shrink**: `body { display: flex; flex-direction: column; min-height: 100vh }` is used — **not** `height: 100%`. Using `height: 100%` caps the body to the viewport; on content-heavy days the flex layout shrinks the nav bar, cutting off day buttons. `.fip-date-nav` has `flex-shrink: 0` to prevent it from being compressed.

**DINPro fonts**: The widget's CSS declares `@font-face` with URLs pointing to `widget.matchscorerlive.com`. Browsers block these cross-origin loads. `generate_html.py` downloads the three `.woff` files to `output/fonts/` once and re-declares them locally so the widget's condensed typeface renders correctly.

**Local time (venue time zone)**: The widget server embeds a static timestamp inside each `<div class="local-time">` at fetch time. The embedded JavaScript clock (`_JS` in `generate_html.py`) overwrites these elements every 30 seconds with the live time in the tournament's own IANA zone (`timezone` in `data/tournaments.json`, e.g. `Europe/Amsterdam`) via `toLocaleTimeString('en-US', {timeZone})` — DST is handled by the browser, no page reload needed. Format: `H:MM AM/PM`. `discover_tournaments.py` derives the zone from the event's "Location" (`_CITY_TIMEZONES` first, for countries with several zones, then `_COUNTRY_TIMEZONES`); an unknown venue gives `null` + a warning, and the page then keeps the widget's own text.

### Name matching (widget ↔ PDF)

Widget names are abbreviated (`A. Sanchez Fallada`); PDF names are full (`Ariana Sanchez Fallada`). `_make_lookup_index()` builds keys from all contiguous sub-sequences of last-name parts so both `M. Calvo` and `M. Santamaria` resolve to `Martina Calvo Santamaria`.

### Caches

| File | Content | TTL |
|---|---|---|
| `data/cache/<slug>/entry_list.json` | Parsed PDF players `{slug: {rank, full_name, …}}`, one per tournament | 24 h; permanent once the tournament is frozen |
| `data/cache/<slug>/matches.json` | Frozen widget data of a finished tournament (`bodies_by_day` before badge injection, `days_matches`, `stylesheet_urls`) | permanent (`--force-refresh` re-fetches) |
| `data/cache/<slug>/stats.json` | Match stats of finished matches `{match_id: stats \| null}` (`null` = no stats, written only once the tournament is frozen) | permanent (`--force-refresh` re-fetches) |
| `output/fonts/` | DINPro `.woff` files, shared by all tournament pages (`../fonts/`) | permanent (delete to re-download) |
| `data/partnerships.json` | Every pair of every entry list | rebuilt when a PDF or `tournaments.json` is newer |
| `data/tournaments.json` | Season tournament list from `discover_tournaments.py` | merged on each run |
| `data/pdfs/<slug>/entry_list_women.pdf` | Local copy of each women's entry list | permanent (re-downloaded if URL changes) |

### Tournament config (`tournaments.py`)

No tournament constants are hardcoded. `tournaments.py` reads `data/tournaments.json` into `Tournament` objects (slug, name, tournament_id, year, start_date, total_days, entry_list_pdf_url; `dates` is derived). Entries without an ID or dates (e.g. postponed) are skipped.

- `main.py --tournament <slug>` selects one; without it `default_candidates()` lists the tournaments whose dates include today (latest start first), then the most recent finished one, and `main.py` takes the first that has women's matches — the next tournament's qualifying can start on the day of a final, with no women's matches yet.
- The `Tournament` is passed explicitly: `scrape_all_days(t)`, `fetch_one_day(t, day)`, `get_today_day(t)`, `fetch_oop_url(t, day)`, and stored in the watch-loop `state["tournament"]`.
- Page title is `f"{t.name} — Women"`.
- `get_rankings_from_pdf(url, cache_path, local_pdf_path)` falls back to `data/pdfs/<slug>/entry_list_women.pdf` if the download fails; with no PDF at all it returns `{}` (no rank badges).

- `tournament_id` is an int, or a **string when it has a leading zero** (Gijón is `"0905"`; the widget returns an empty schedule for `905`).

New season / new tournament: run `python discover_tournaments.py --year <year>` — no code edits.

### Output layout and `--all`

```
output/
├── index.html          # landing page: one poster card per tournament (name, tier, dates, status)
├── assets/theme.css    # design tokens (CSS variables only) — linked by every page
├── assets/overrides.css # match-card restyling, scoped under .fip-theme — tournament pages only
├── assets/premier-padel-logo.svg # landing header logo; links to the season's padelfip.com calendar
├── fonts/              # shared DINPro fonts
└── <slug>/index.html   # one page per tournament, with a "← All tournaments" link
```

- `python main.py --all` generates every tournament in `data/tournaments.json`; without `--all` only one tournament page is generated. Both rewrite the landing page (`_write_landing()`), which links only to pages that exist on disk.
- `--output FILE` (single tournament only) writes a standalone page: fonts next to the file, no back link, no landing page.
- **Frozen cache**: `Tournament.is_frozen()` is true from the second day after the last day (a final in the Americas can still be live after local midnight). A frozen tournament is fetched once, saved to `data/cache/<slug>/matches.json`, and later runs make **zero HTTP requests** for it. It is frozen only if it has matches and no day failed with a non-404 error.
- **Landing statuses**: Finished / Ongoing / Upcoming from the dates; `No data` = finished but no page; `Postponed` comes from `tournaments.json`. Upcoming tournaments are probed with one request (day 1) per run and get a page as soon as the schedule exists.

### Tournament discovery (`discover_tournaments.py`)

Standalone; writes the `data/tournaments.json` that `main.py` reads. `requests + BeautifulSoup` only, 1 s between requests.

- **Calendar** (`/calendar-premier-padel/?events-year={year}`) only yields event URLs. Tier and ID are on the **event page**: `class="event category-event-fip-ppt-p1 idEvent_2209"`. Finals use `category-event-fip-pp-master-finals`.
- **Dates**: `p.overview__text` under "Qualification" / "Main draw" (else the `MAIN DRAW …` / `QUALIFIERS …` lines of the "General info:" block); fallback is the header `div.event__date` (`DD/MM/YYYY - DD/MM/YYYY`, or `POSTPONED`). When the two disagree, the one matching the OOP widget's `totalday` wins (from the JSON-escaped `fetchUrl: "…get-oop-data.php?…&totalday=8…"`).
- **Entry list PDF** is loaded by JS: `POST /wp-admin/admin-ajax.php` with `action=load_entrylist_tab`, `security=<nonce from var padelfip_ajax>`, `post_id=<#entrylist-ajax-container data-post-id>`. The response `data.html` contains `var pdfMap = {"M": {"": url}, "W": {"": url}}` — women's PDF is `pdfMap.W[""]`. On 403 the nonce is refreshed once via `action=padelfip_refresh_nonce`.
- **Merge**: existing non-null values in `data/tournaments.json` win (manual edits survive); `status` (finished / ongoing / upcoming / postponed) is always recomputed; `start_date` / `end_date` / `total_days` are replaced whenever the site gives them (FIP moves qualifying days, and a stale start shifts every widget day); `--overwrite` lets scraped values replace them. The PDF is re-downloaded only when missing, when its URL changed, or with `--overwrite`.

### Partner change tracking (`partnerships.py`)

- **Source**: the local entry list PDFs (`data/pdfs/<slug>/entry_list_women.pdf`). `_parse_pdf_pairs()` in `scrape_rankings.py` returns pairs with their section (MAIN DRAW / QUALIFICATIONS / WAITING LIST); `_parse_pdf_players()` is built on it. All three sections count as history — waiting-list pairs do sometimes play.
- **`data/partnerships.json`**: one row per pair per tournament: `{tournament_slug, section, player_slug, player_name, partner_slug, partner_name}`. `load_partnerships()` rebuilds it automatically when a PDF or `tournaments.json` is newer.
- **Name variants**: `_canonical_slugs()` merges a name whose words are a prefix of a longer one (`marta-borrero-fernandez` → `…-de-la-puente`).
- **Rule**: `previous_partners(rows, slug)` gives each player's partner in the most recent *earlier* tournament she entered (not just the previous one on the calendar). A team on a match card is a new pair when either player's previous partner differs from her partner on the card. First tournament of the season → no history → no badge.
- **Card → player**: `new_pair_checker` resolves the two widget names against that tournament's entry list. An ambiguous name (`A. Martinez`) is settled by the listed pair it forms with the other player; if a player cannot be resolved (wild cards not in the PDF, unusual spellings) **no badge is shown**.
- **Display**: `inject_new_pair_badges()` in `generate_html.py` appends `<button class="fip-new-pair">` (link icon + "New pair") to the team's `div.player-names`, under the two names. The button holds a `span.fip-new-pair-tip` card shown on hover/focus: "New partnership" / `Previously with M. Calvo` / `at Buenos Aires P1` — parsed from the checker's tooltip text (`Previously with M. Calvo (Buenos Aires P1)`, or one `Name: previously with …` line per player when both have a history). `main._inject_badges()` applies rank badges, pair badges, then `inject_emoji()`, for both the first run and watch updates.

### Match stats (`scrape_stats.py`)

- **Source**: the widget loads stats on click with `POST /screen/getmatchstats?t=tol` (form-encoded `matchId, year, tournamentId, organization` — all on the card's `a.open` link as `data-id / data-year / data-tid / data-org`). The endpoint sends **no CORS header**, so the page cannot call it: Python fetches, `parse_stats_html()` turns the fragment into `{score, time, teams, periods: [{name, sections: [{title, rows: [[label, a, b]]}]}]}` (periods = Match / Set 1 / Set 2 …).
- **`MatchStats`** (one per tournament, in `state["stats"]`): a finished match is fetched once and kept in `stats.json`; a live match is fetched again on every `update()` and never written to disk; a match not started is never requested. A finished match without stats is retried on each run until the tournament is frozen, then stored as `null` — a frozen tournament makes zero stats requests from its second run on. 0.3 s between requests; a failed request skips that match only.
- **Watch loop**: `_watch_update()` calls `update([body])` for today's day only (live matches + matches that just finished).
- **Display**: `inject_match_stats()` (last step of `main._inject_badges()`) replaces each `a.open` with `<button class="fip-stats-btn" data-match="WD003">` — or removes the link when there are no stats — and appends the day's stats as `<script type="application/json" class="fip-stats-data">` inside the day panel, so the in-place refresh carries it along. `_JS` draws them in the page's single `<dialog id="fip-stats-dialog">` (tabs per period, the higher value highlighted) and redraws an open pop-up after a refresh when the numbers changed.

### Design layer (DESIGN_ROADMAP.md, phases D1–D6)

- **Tokens**: `output/assets/theme.css` holds every color, font and spacing value as a CSS variable. No literal colors in `generate_html.py` or `overrides.css` — use `var(--…)` (and `color-mix()` for tints). Both asset files are hand-edited sources that live in `output/assets/`; `_theme_link_tags()` links them and copies them next to a standalone `--output` page.
- **Match cards are restyled by CSS only**: `output/assets/overrides.css`, loaded after the widget's stylesheets, every rule under `.fip-theme` (the class on `#fip-panels-wrapper`). Each `table.w-100` becomes a flex column (summary row pinned with `margin-top: auto`). From 768 px up the court columns dissolve (`display: contents`) into one grid so cards in the same row are equally tall; each court keeps its own grid column via `:nth-child`. The widget CSS uses `!important` for fonts and cell padding, so a few overrides need it too.
- **Current game points**: a live match has a `td.points` cell (`30`, `40`, `Ad`) between the team and the sets, shown as an outlined box. Other rows have an empty spacer `td` there, which stays hidden.
- **"Women" label** in the card header is hidden by CSS (`.round-name b`) — the element must stay in the HTML, the gender filter reads it.
- **Emoji** (`inject_emoji()`): a flag emoji span before each `img.flags` (code from the image file name via `_COUNTRY_ISO2`; unknown code → the image stays), 🏅 for the winners of a completed match, 👑 only when the round text is exactly "Final". Rendered with Noto Color Emoji (`--font-emoji`) because Windows has no flag emoji.
- **Landing cards / page header**: poster from `image_url` in `data/tournaments.json` (`og:image`, or the event page's poster when `og:image` is landscape or missing); no image → court-line drawing. On the landing page the "Premier Padel Women" brand reloads the page and scrolls to `#live`, the month section of the ongoing tournament (no ongoing tournament → reload, top of the page).
- **Local time** shows `6:34 PM · CEST (UTC+2)`; the abbreviation comes from `Intl`, and zones without one show the offset alone (`UTC−3`).
- Widget pages are cached hard by browsers when served by a plain static server — use `--serve` (no-store headers) or a hard refresh when checking CSS changes.

### Widget HTML selectors (break if matchscorerlive.com changes structure)

| Selector | Used for |
|---|---|
| `div.line-thin` | Player name container — badge injection target |
| `td.team` / `div.mr-2` | One team per `td.team`; NEW PAIR badge goes into its right-hand `div.mr-2` |
| `table.w-100` | One match per table — gender filter + empty-day detection |
| `div.round-name > b` | Category text ("Women" / "Men") |
| `div.col-lg-4.col-md-6` | Court columns |
| `div.local-time` | Local time display in each court header |
| `a.open[data-id]` | "MATCH STATS" link in `tr.summary` — match id and request values for the stats |
| `tr.scorebox-header-completed` / `img.ballg` | Finished / live match — which stats to fetch |

### `--watch` + `--serve` mode

- **First run**: `_initial_generation()` fetches all tournament days (or loads the frozen cache), builds `state` dict (tournament, bodies_by_day, stylesheet_urls, rankings, ranking_index, days_matches) in memory. `states` maps slug → state.
- **Each cycle**: `_watch_cycle()` calls `_watch_update()` only for tournaments where `is_refreshable()` (start date … last day + 1). It fetches **only today's day** (1 HTTP request), updates `state["bodies_by_day"][today_day]` in place, rewrites HTML. Finished and upcoming tournaments are not touched.
- **Live detection**: if any match in today's data has `status == "in_progress"` (widget contains `img.ballg`), `refresh_interval` drops to 15 s; otherwise 60 s. The `<meta name="fip-refresh">` in the generated HTML is rewritten each cycle to match.
- **Refresh without reload**: the page does not reload itself. `_JS` fetches its own URL every `fip-refresh` seconds and swaps only the `.fip-day-panel`s whose HTML changed, so the selected day and scroll position stay put. It reloads fully only when the inline script itself changed, or on `file://` (fetch is blocked there); `<noscript>` keeps a `<meta http-equiv="refresh">` fallback.
- **Chosen day survives reloads**: the clicked day is stored in `sessionStorage` (`fip-day:<pathname>`) and restored on load. Clicking the page's default day clears it, so that tab follows the newest day again.
- **`--serve`**: daemon-thread `HTTPServer` on port 8080 serving `output/` with `Cache-Control: no-store` so the browser always loads the freshly written file. `/` is the landing page, `/<slug>/` a tournament page.
- `--force-refresh` applies only to the first run (PDF downloads and frozen match caches); subsequent cycles use the caches.
