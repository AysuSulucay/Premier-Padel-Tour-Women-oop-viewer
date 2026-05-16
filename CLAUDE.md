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

# Debug PDF parsing when a new tournament's PDF layout differs
python -c "from scrape_rankings import _debug_pdf_rows; _debug_pdf_rows()"

# Windows encoding fix if terminal shows garbled characters
set PYTHONUTF8=1 && python main.py --serve --watch --open
```

## Architecture

The tool is a **4-step pipeline** — each step is a separate module, orchestrated by `main.py`.

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

**Widget HTML pass-through**: The output page does **not** re-render match cards. It keeps the widget's own HTML verbatim (CSS included via `<link>` tags copied from the widget's `<head>`). Only FIP rank badges and the date-nav bar are custom elements. This means widget structure changes will break parsing without breaking visual design.

**Gender filter operates on HTML, not parsed data**: `filter_gender_html()` removes `<table class="w-100">` blocks where the `<b>` tag ≠ "Women". The structured `parse_widget()` function (used only for match counts/logging) is separate.

**BeautifulSoup fragment handling**: Both `filter_gender_html` and `inject_rank_badges` return `body_el.decode_contents()` instead of `str(soup)`. Using `str(soup)` causes `html.parser` to wrap fragments in `<html><body>` tags that then get embedded inside day-panel `<div>`s, producing invalid nested documents.

**Inline `<style>` stripping**: Widget body HTML contains `<style>` tags that would pollute the output page's CSS globally. `extract_widget_body()` strips all of them (both direct children of `<body>` and any nested inside the court row). All needed card styling is in the linked `.css` files.

**Sticky footer / nav shrink**: `body { display: flex; flex-direction: column; min-height: 100vh }` is used — **not** `height: 100%`. Using `height: 100%` caps the body to the viewport; on content-heavy days the flex layout shrinks the nav bar, cutting off day buttons. `.fip-date-nav` has `flex-shrink: 0` to prevent it from being compressed.

**DINPro fonts**: The widget's CSS declares `@font-face` with URLs pointing to `widget.matchscorerlive.com`. Browsers block these cross-origin loads. `generate_html.py` downloads the three `.woff` files to `output/fonts/` once and re-declares them locally so the widget's condensed typeface renders correctly.

**Local time (Buenos Aires)**: The widget server embeds a static timestamp inside each `<div class="local-time">` at fetch time. The embedded JavaScript clock (`_JS` in `generate_html.py`) overwrites these elements with live Buenos Aires time (UTC−3, no DST) every 30 seconds — no page reload needed. Format: `H:MM AM/PM`. The footer generation timestamp also uses ART: `datetime.now(timezone(timedelta(hours=-3)))`.

### Name matching (widget ↔ PDF)

Widget names are abbreviated (`A. Sanchez Fallada`); PDF names are full (`Ariana Sanchez Fallada`). `_make_lookup_index()` builds keys from all contiguous sub-sequences of last-name parts so both `M. Calvo` and `M. Santamaria` resolve to `Martina Calvo Santamaria`.

### Caches

| File | Content | TTL |
|---|---|---|
| `data/entry_list_cache.json` | Parsed PDF players `{slug: {rank, full_name, …}}` | 24 h |
| `output/fonts/` | DINPro `.woff` files | permanent (delete to re-download) |

### Updating for a new tournament

In `scrape_matches.py`:
```python
TOURNAMENT_START      = date(2026, 5, 10)
TOURNAMENT_ID         = 2209          # from OOP widget embed URL
TOURNAMENT_YEAR       = 2026
TOURNAMENT_TOTAL_DAYS = 8
TOURNAMENT_DATES      = [date(2026, 5, d) for d in range(10, 18)]
```

In `main.py`:
```python
DEFAULT_TOURNAMENT = "Premier Padel Buenos Aires P1 2026 — Women"
ENTRY_LIST_PDF_URL = "https://www.padelfip.com/wp-content/uploads/.../Entry-list-....pdf"
```

### Widget HTML selectors (break if matchscorerlive.com changes structure)

| Selector | Used for |
|---|---|
| `div.line-thin` | Player name container — badge injection target |
| `table.w-100` | One match per table — gender filter + empty-day detection |
| `div.round-name > b` | Category text ("Women" / "Men") |
| `div.col-lg-4.col-md-6` | Court columns |
| `div.local-time` | Local time display in each court header |

### `--watch` + `--serve` mode

- **First run**: `_initial_generation()` fetches all 8 tournament days, builds `state` dict (bodies_by_day, stylesheet_urls, rankings, ranking_index, days_matches) in memory.
- **Each cycle**: `_watch_update()` fetches **only today's day** (1 HTTP request), updates `state["bodies_by_day"][today_day]` in place, rewrites HTML.
- **Live detection**: if any match in today's data has `status == "in_progress"` (widget contains `img.ballg`), `refresh_interval` drops to 15 s; otherwise 60 s. The `<meta http-equiv="refresh">` in the generated HTML is rewritten each cycle to match.
- **`--serve`**: daemon-thread `HTTPServer` on port 8080 with `Cache-Control: no-store` so the browser always loads the freshly written file.
- `--force-refresh` applies only to the first run's PDF download; subsequent cycles use the 24-hour cache.
