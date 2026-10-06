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

# Every tournament of the current season + landing page (finished ones come from the frozen cache)
python main.py --all --serve --watch --open

# Another season, or every season (2023 onwards) — the landing page always lists all of them
python main.py --all --year 2024
python main.py --all --year all

# Debug PDF parsing when a new tournament's PDF layout differs
python -c "from scrape_rankings import _debug_pdf_rows; from tournaments import get_tournament; _debug_pdf_rows(get_tournament('buenos-aires-p1-2026').entry_list_pdf_url)"

# Discover all Premier Padel tournaments of a season → data/tournaments.json + data/pdfs/ (2023 is the first season)
python discover_tournaments.py --year 2026
python discover_tournaments.py --year 2026 --only buenos-aires-p1-2026   # single tournament
python discover_tournaments.py --year 2026 --overwrite                   # scraped values replace existing ones

# Rebuild data/partnerships.json from the entry list PDFs and list every partner change
python partnerships.py

# Checks for the live feed's "when to ask again" rules
python test_live_feed.py

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

A list can spell a name shorter than the widget (`Aida Martinez` / `A. Martinez Sanjuan`): `build_lookup_index(cache, long_names)` also indexes each player under her longest known name — the canonical names of the tournament's rows in `data/partnerships.json` — so the card finds her and not the higher-ranked `Araceli Martinez`. A name that still fits several players gets the highest-ranked one.

### Profile links (`add_profile_urls` in `scrape_rankings.py`)

The rank on a badge comes from the tournament's entry list (her rank at that time); the badge **link** does not. A profile's address is the slug of the name as FIP spells it (`/player/gemma-triay-pons/`), an entry list can spell it shorter (`Gemma Triay`), and padelfip.com redirects an unknown slug to some similar page (`/player/gemma-triay/` → a pair page). So:

- `data/fip_profiles.json` holds the women's FIP ranking list `{profile slug: full name}` (`ranking/load-more` API, 1,000 per request — a larger `limit` returns nothing; fetched again when older than 24 h, once per run).
- Each entry list player is looked up under her fuller name from other lists (`_long_forms()`, the same rule as the lookup index), else under the list's own spelling (after `player_aliases.json`): `_ranked_profile()` takes the ranked player with the same name, else the one whose name is hers with words left out or the other way round (`Maria Virginia Riera` / FIP `Virginia Riera`), dropped trailing surnames first. FIP's name counts, not its slug (`kae-tokumoto-2`).
- Not ranked any more: the name's own slug is requested once (`_profile_exists()`, 200 = profile, redirect or 404 = none) and the answer kept in `checked` for good — delete the entry to ask again.
- No profile found, or a name that fits several known players (`Cristina Gonzalez`): `profile_url` is `''` and the badge is a `<span>` instead of a link. `entry_list.json` no longer stores an address; it is set on each run.
- `python scrape_rankings.py` runs the self-check of `_ranked_profile()`.

### Entry list PDF parsing (`_parse_pdf_pairs` in `scrape_rankings.py`)

The layout changed several times between 2023 and 2026, so the parser works on **word positions**, not text lines:

- A pair is the block of rows above its **points row** (`17660 points 17660 points`; also `N/A points`, `78points`, a trailing `PR`). Both player columns are left-aligned and the points start each column: `_player_columns()` takes the two column edges from the x of the points entries (per page, else per document).
- `_block_players()` assigns the block's words by x: numbers left of column 1 are position + ranking 1, a number between the columns is ranking 2, every other word from a column's edge on is that player's name — also when the name wraps onto a second line. A trailing 2–3 capital-letter word is the nationality (absent in two 2025 lists). `PR`, `WC`, `N/A`, `Lli` / `LL i` are markers, not name words.
- Two older layouts have one pair per line: `_SLASH_ROW_RE` (Italy Major / Madrid P1 2023: `NAME // NAME ESP/ESP pts pts total`, no rankings) and `_ONE_LINE_ROW_RE` (Finals 2024).
- `rank` is None when the list has no ranking for the player (no ranking column: Italy Major and Madrid P1 2023, Brussels and Sevilla P2 2024; `N/A`; 0) — the pair still counts for partner tracking, the card gets no rank badge. A block that cannot be read (letter-spaced text) is skipped.
- Sections: the first pairs are `MAIN DRAW` even without a heading (2024 lists have none).
- Checking a parser change: parse every `data/pdfs/*/entry_list_women.pdf` before and after and compare the pairs — no existing pair may be lost or altered.

### Caches

| File | Content | TTL |
|---|---|---|
| `data/cache/<slug>/entry_list.json` | Parsed PDF players `{slug: {rank, full_name, …}}`, one per tournament | 24 h; permanent once the tournament is frozen (delete the file to re-parse the local PDF, e.g. after a parser change) |
| `data/cache/<slug>/matches.json` | Frozen widget data of a finished tournament (`bodies_by_day` before badge injection, `days_matches`, `stylesheet_urls`); every day empty = no women's draw | permanent (`--force-refresh` re-fetches) |
| `data/cache/<slug>/stats.json` | Match stats of finished matches `{match_id: stats \| null}` (`null` = no stats, written only once the tournament is frozen) | permanent (`--force-refresh` re-fetches) |
| `output/fonts/` | DINPro `.woff` files, shared by all tournament pages (`../fonts/`) | permanent (delete to re-download) |
| `data/partnerships.json` | Every pair of every entry list | rebuilt when a PDF, `tournaments.json` or `player_aliases.json` is newer (not after a parser change: run `python partnerships.py`) |
| `data/player_aliases.json` | Hand-kept `{variant slug: usual slug}` for name spellings no rule connects | manual |
| `data/fip_profiles.json` | `ranked`: women's FIP ranking list `{profile slug: full name}`; `checked`: `{slug: does padelfip.com have a profile there}` for unranked players | `ranked` 24 h (`--force-refresh` re-fetches); `checked` permanent |
| `data/tournaments.json` | Season tournament list from `discover_tournaments.py` | merged on each run |
| `data/pdfs/<slug>/entry_list_women.pdf` | Local copy of each women's entry list | permanent (re-downloaded if URL changes) |

### Tournament config (`tournaments.py`)

No tournament constants are hardcoded. `tournaments.py` reads `data/tournaments.json` into `Tournament` objects (slug, name, tournament_id, year, start_date, total_days, entry_list_pdf_url; `dates` is derived). Entries without an ID or dates (e.g. postponed) are skipped.

- `main.py --tournament <slug>` selects one; without it `default_candidates()` lists the tournaments whose dates include today (latest start first), then the most recent finished one, and `main.py` takes the first that has women's matches — the next tournament's qualifying can start on the day of a final, with no women's matches yet.
- The `Tournament` is passed explicitly: `scrape_all_days(t)`, `fetch_one_day(t, day)`, `get_today_day(t)`, `fetch_oop_url(t, day)`, and stored in the watch-loop `state["tournament"]`.
- Page title is `f"{t.name} — Women"`. `tournaments.short_name()` gives the name for cards, page headings and tooltips: "Premier Padel" and the season are dropped wherever they stand (`BNL Italy Major Premier Padel 2023` → `BNL Italy Major`).
- `get_rankings_from_pdf(url, cache_path, local_pdf_path)` falls back to `data/pdfs/<slug>/entry_list_women.pdf` if the download fails; a frozen tournament reads that local copy without trying the download. With no PDF at all it returns `{}` (no rank badges).

- `tournament_id` is an int, or a **string when it has a leading zero** (Gijón 2026 is `"0905"`; the widget returns an empty schedule for `905` — while Gijón 2025 is `905`). IDs repeat across seasons (`902`, `5101`): the widget URL carries the year and every cache is keyed by slug.

New season / new tournament: run `python discover_tournaments.py --year <year>` — no code edits.

### Output layout and `--all`

```
output/
├── index.html          # landing page: every season, one poster card per tournament (name, tier, dates, status)
├── assets/theme.css    # design tokens (CSS variables only) — linked by every page
├── assets/overrides.css # match-card restyling, scoped under .fip-theme — tournament pages only
├── assets/premier-padel-logo.svg # landing header logo; links to the season's padelfip.com calendar
├── fonts/              # shared DINPro fonts
└── <slug>/index.html   # one page per tournament, with a "← All tournaments" link
```

- `python main.py --all` generates every tournament of the current season (today's year, else the newest); `--all --year 2024` another season, `--all --year all` every tournament in `data/tournaments.json`. Without `--all` only one tournament page is generated. All of them rewrite the landing page (`_write_landing()`), which covers every season and links only to pages that exist on disk.
- `--output FILE` (single tournament only) writes a standalone page: fonts next to the file, no back link, no landing page.
- **Frozen cache**: `Tournament.is_frozen()` is true from the second day after the last day (a final in the Americas can still be live after local midnight). A frozen tournament is fetched once, saved to `data/cache/<slug>/matches.json`, and later runs make **zero HTTP requests** for it (the FIP ranking list for the profile links is fetched once per run, at most every 24 h). It is frozen only if the schedule has matches (of any category — `scrape_all_days()` returns that count as its fifth value) and no day failed with a non-404 error.
- **No women's draw**: a frozen cache whose days are all empty means the tournament was played without women (Qatar Major 2023, Mendoza P1 2023). `_no_womens_draw()` → no page, and the tournament is left off the landing page. A schedule with no match at all is not frozen: the ID may be wrong.
- **Landing statuses**: Finished / Ongoing / Upcoming from the dates; `No data` = finished but no page and no frozen cache; `Postponed` comes from `tournaments.json`. Upcoming tournaments are probed with one request (day 1) per run and get a page as soon as the schedule exists. A season is listed once it has a page or a tournament still to be played.
- **Landing filter** (`generate_landing_html()` + `_LANDING_JS`, design in `design/reference/`): every season's month sections are in the page, newest season first (`section.fip-month[data-year][data-month]`, month = the month the tournament starts in, `tbc` for undated ones); the script shows one season with all its months. The filter bar is `position: sticky`; a month tab scrolls to that month's section (same idea as the padelfip.com calendar), and while the page scrolls the selected tab follows the month under the bar. State lives in the URL hash (`#2025-03`, `#2026-tbc`) and decides where the page opens (`history.scrollRestoration = 'manual'`); no hash = this year, scrolled to this month. `#live` selects the month of the ongoing tournament (`data-live` on the nav). A tab click's own scrolling does not move the selection (`quiet` timer) — a month near the page end cannot reach the bar. The opening month is aligned again once the fonts are loaded, unless the visitor already scrolled. Tournament pages link back with their own hash (`../index.html#2026-09`). Without JavaScript the filter bar stays hidden and every season is visible. Smooth scrolling and scroll events do not run in a hidden browser pane — check the filter in a visible tab.

### Tournament discovery (`discover_tournaments.py`)

Standalone; writes the `data/tournaments.json` that `main.py` reads. `requests + BeautifulSoup` only, 1 s between requests.

- **Calendar** (`/calendar-premier-padel/?events-year={year}`) only yields event URLs. Tier and ID are on the **event page**: `class="event category-event-fip-ppt-p1 idEvent_2209"`. Finals use `category-event-fip-pp-master-finals`.
- **Dates**: `p.overview__text` under "Qualification" / "Main draw" (else the `MAIN DRAW …` / `QUALIFIERS …` lines of the "General info:" block; older pages write `9-11/03/2025` or `29th September`); fallback is the header `div.event__date` (`DD/MM/YYYY - DD/MM/YYYY`, or `POSTPONED`). When the two disagree, the one matching the OOP widget's `totalday` wins (from the JSON-escaped `fetchUrl: "…get-oop-data.php?…&totalday=8…"`).
- **Older seasons**: `--year 2023` … `2025` work the same way (the calendar has no 2022 page). Some events have no women's entry list (Qatar Major 2023, Mendoza P1 2023, Acapulco P1 2024) and some slugs carry no year (`premier-padel-cancun-p2`).
- **Entry list PDF** is loaded by JS: `POST /wp-admin/admin-ajax.php` with `action=load_entrylist_tab`, `security=<nonce from var padelfip_ajax>`, `post_id=<#entrylist-ajax-container data-post-id>`. The response `data.html` contains `var pdfMap = {"M": {"": url}, "W": {"": url}}` — women's PDF is `pdfMap.W[""]`. On 403 the nonce is refreshed once via `action=padelfip_refresh_nonce`.
- **Merge**: existing non-null values in `data/tournaments.json` win (manual edits survive); `status` (finished / ongoing / upcoming / postponed) is always recomputed; `start_date` / `end_date` / `total_days` are replaced whenever the site gives them (FIP moves qualifying days, and a stale start shifts every widget day); `--overwrite` lets scraped values replace them. The PDF is re-downloaded only when missing, when its URL changed, or with `--overwrite`.

### Partner change tracking (`partnerships.py`)

- **Source**: the local entry list PDFs (`data/pdfs/<slug>/entry_list_women.pdf`). `_parse_pdf_pairs()` in `scrape_rankings.py` returns pairs with their section (MAIN DRAW / QUALIFICATIONS / WAITING LIST); `_parse_pdf_players()` is built on it. All three sections count as history — waiting-list pairs do sometimes play.
- **`data/partnerships.json`**: one row per pair per tournament: `{tournament_slug, section, player_slug, player_name, partner_slug, partner_name}`. `load_partnerships()` rebuilds it automatically when a PDF or `tournaments.json` is newer.
- **Name variants**: `_canonical_slugs()` merges a name that is a longer one with words left out, the first or the last word kept (`marta-borrero` → `marta-borrero-fernandez-de-la-puente`, `virginia-riera` → `maria-virginia-riera`) — when it fits one player only; dropped trailing surnames are tried first. `cristina-gonzalez` fits two players and stays apart. `data/player_aliases.json` connects typos and abbreviations (`…-fdz-…`, `goyenche`).
- **Rule**: `previous_partners(rows, slug)` gives each player's partner in the most recent *earlier* tournament she entered (not just the previous one on the calendar) — **across seasons**; a tournament of another season is named with its year (`Qatar Airways Finals 2025`). A team on a match card is a new pair when either player's previous partner differs from her partner on the card. Only the very first tournament (Italy Major 2023) has no history.
- **Card → player**: `new_pair_checker` resolves the two widget names against that tournament's entry list. An ambiguous name (`A. Martinez`) is settled by the listed pair it forms with the other player; if a player cannot be resolved (wild cards not in the PDF, unusual spellings) **no badge is shown**.
- **Display**: `inject_new_pair_badges()` in `generate_html.py` appends `<button class="fip-new-pair">` (link icon + "New pair") to the team's `div.player-names`, under the two names. The button holds a `span.fip-new-pair-tip` card shown on hover/focus: "New partnership" / `Previously with M. Calvo` / `at Buenos Aires P1` — parsed from the checker's tooltip text (`Previously with M. Calvo (Buenos Aires P1)`, or one `Name: previously with …` line per player when both have a history). `generate_html.inject_badges()` applies rank badges, pair badges, then `inject_emoji()`, for both the first run and watch updates.

### Match stats (`scrape_stats.py`)

- **Source**: the widget loads stats on click with `POST /screen/getmatchstats?t=tol` (form-encoded `matchId, year, tournamentId, organization` — all on the card's `a.open` link as `data-id / data-year / data-tid / data-org`). The endpoint sends **no CORS header**, so the page cannot call it: Python fetches, `parse_stats_html()` turns the fragment into `{score, time, teams, periods: [{name, sections: [{title, rows: [[label, a, b]]}]}]}` (periods = Match / Set 1 / Set 2 …).
- **`MatchStats`** (one per tournament, in `state["stats"]`): a finished match is fetched once and kept in `stats.json`; a live match is fetched again on every `update()` and never written to disk; a match not started is never requested. A finished match without stats is retried on each run until the tournament is frozen, then stored as `null` — a frozen tournament makes zero stats requests from its second run on. 0.3 s between requests; a failed request skips that match only. In a frozen tournament no match counts as live (the widget left a few old matches marked live for good).
- **Watch loop**: `_watch_update()` calls `update([body])` for today's day only (live matches + matches that just finished).
- **Display**: `inject_match_stats()` (last step of `inject_badges()`) replaces each `a.open` with `<button class="fip-stats-btn" data-match="WD003">` — or removes the link when there are no stats — and appends the day's stats as `<script type="application/json" class="fip-stats-data">` inside the day panel, so the in-place refresh carries it along. `_JS` draws them in the page's single `<dialog id="fip-stats-dialog">` (tabs per period, the higher value highlighted) and redraws an open pop-up after a refresh when the numbers changed.

### Hosting and live feed (Vercel — `live_feed.py`, `api/live.py`)

- **Site**: Vercel project `premier-padel-women` (https://premier-padel-women-oop.vercel.app) serves `output/` as it is in git — output directory and "no build" are project settings; every push to `main` deploys. `vercel.json` only configures the function.
- **`/api/live?t=<slug>`** (`api/live.py`, a thin handler around `live_feed.answer()`): today's day of a tournament being played as `{day, html, next_at}` — `html` is the inner HTML of `#fip-day-<day>`, built by the same `fetch_day()` → `inject_badges()` → `panel_content()` as the static page. Outside the tournament `{day: null, next_at: null}` without any request to the widget; an unknown slug is 404.
- **Read-only host**: the function reads `data/cache/<slug>/entry_list.json`, `stats.json`, `data/partnerships.json` and `data/fip_profiles.json` as deployed — no PDF, no download, nothing written (`badge_state(offline=True)`, `MatchStats(persist=False)`; `pdfplumber` is imported only where a PDF is parsed and is not in `api/requirements.txt`). Stats of today's finished matches live in the instance's memory: a new instance fetches them once more.
- **When to ask again** (`next_check()`, venue time): a women's match live → 15 s; one that can start any moment ("Followed by", or its time has come) → 2 min; "Starting at" → 10 min before; "Not before" → at that time; the women's day over → the next day; no schedule yet → 10 min. A match cannot start before the match ahead of it on its court — men's matches count for that. The tournament day changes at 04:00 venue time (`DAY_ROLLOVER_HOUR`), so a night session past midnight is still "today".
- **The page** (`_JS`): a page written by a one-shot run for a tournament that is not frozen carries `<meta name="fip-live" content="/api/live?t=<slug>">` (`live_url` of `generate_html()`). Its script then asks the feed instead of fetching itself: it swaps `#fip-day-<day>` when the HTML changed, follows a new day that has women's matches (unless the viewer chose a day), and sets its timer to `next_at`; `next_at: null` or a 404 ends it, any other failure is retried after 60 s. "Now" is the answer's `Date` + `Age` headers — a cached answer keeps the `Date` it was made at. `--watch` pages and `--output` pages have no such meta and refresh as described under "`--watch` + `--serve` mode".
- **Locally**: `python main.py --serve` (without `--watch`) answers `/api/live` too, so the hosted behaviour can be tried in the browser.
- **Caching**: the answer carries `Cache-Control: s-maxage=<seconds until next_at>`, so Vercel's CDN sends the widget one request per interval whatever the number of viewers; `answer()` also reuses its last answer per slug inside the instance (a changed query string does not get past it).

### Design layer (DESIGN_ROADMAP.md, phases D1–D6)

- **Tokens**: `output/assets/theme.css` holds every color, font and spacing value as a CSS variable. No literal colors in `generate_html.py` or `overrides.css` — use `var(--…)` (and `color-mix()` for tints). Both asset files are hand-edited sources that live in `output/assets/`; `_theme_link_tags()` links them and copies them next to a standalone `--output` page.
- **Match cards are restyled by CSS only**: `output/assets/overrides.css`, loaded after the widget's stylesheets, every rule under `.fip-theme` (the class on `#fip-panels-wrapper`). Each `table.w-100` becomes a flex column (summary row pinned with `margin-top: auto`). From 768 px up the court columns dissolve (`display: contents`) into one grid so cards in the same row are equally tall; each court keeps its own grid column via `:nth-child`. The widget CSS uses `!important` for fonts and cell padding, so a few overrides need it too.
- **Current game points**: a live match has a `td.points` cell (`30`, `40`, `Ad`) between the team and the sets, shown as an outlined box. Other rows have an empty spacer `td` there, which stays hidden.
- **Default day**: a tournament page opens on the last day with women's matches, not the last day with a schedule — a men's final can be played a day later (Riyadh P1 2025).
- **"Women" label** in the card header is hidden by CSS (`.round-name b`) — the element must stay in the HTML, the gender filter reads it.
- **Emoji** (`inject_emoji()`): a flag emoji span before each `img.flags` (code from the image file name via `_COUNTRY_ISO2`; unknown code → the image stays), 🏅 for the winners of a completed match, 👑 only when the round text is exactly "Final". Rendered with Noto Color Emoji (`--font-emoji`) because Windows has no flag emoji.
- **Landing cards / page header**: poster from `image_url` in `data/tournaments.json` (`og:image`, or the event page's poster when `og:image` is landscape or missing); no image → court-line drawing. On the landing page the "Premier Padel Women" brand reloads the page with `#live`: the season and month of the ongoing tournament are selected (no ongoing tournament → this season and this month).
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
- **`--serve`**: daemon-thread `ThreadingHTTPServer` on port 8080 serving `output/` with `Cache-Control: no-store` so the browser always loads the freshly written file. `/` is the landing page, `/<slug>/` a tournament page.
- `--force-refresh` applies only to the first run (PDF downloads and frozen match caches); subsequent cycles use the caches.
