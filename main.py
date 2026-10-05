"""
FIP Order of Play Generator -- entry point.

Usage:
  python main.py --serve --watch --open   # recommended live mode
  python main.py --watch --open           # live mode via file://
  python main.py                          # single run
  python main.py --force-refresh --open
  python main.py --tournament buenos-aires-p1-2026   # pick a tournament from data/tournaments.json
  python main.py --all --open             # every tournament + landing page
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scrape_matches import scrape_all_days, fetch_one_day, get_today_day
from scrape_rankings import get_rankings_from_pdf, build_lookup_index
from generate_html import (
    generate_html, generate_landing_html, inject_rank_badges, inject_new_pair_badges, inject_emoji,
    inject_match_stats,
)
from scrape_stats import MatchStats
from partnerships import load_partnerships, new_pair_checker
from tournaments import TOURNAMENTS_PATH, Tournament, default_candidates, get_tournament, load_entries, load_tournaments

OUTPUT_DIR     = Path("output")   # output/index.html = landing, output/<slug>/index.html = tournament
WATCH_INTERVAL = 60   # seconds between updates — no live match
LIVE_INTERVAL  = 15   # seconds between updates — live match in progress
SERVE_PORT     = 8080


# ── Browser opener ────────────────────────────────────────────────────────────

def _open_browser(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)
    elif sys.platform == "darwin":
        os.system(f'open "{url}"')
    else:
        os.system(f'xdg-open "{url}"')


# ── HTTP server (no-cache) ────────────────────────────────────────────────────

def _start_server(root_dir: Path, index_file: Path, port: int = SERVE_PORT) -> str:
    """Serve *root_dir* over HTTP with Cache-Control: no-store.

    ``/`` serves *index_file*; a directory serves its ``index.html``.
    Returns the base URL. Runs in a daemon thread so it exits when the main process ends.
    """
    root  = Path(root_dir).resolve()
    index = Path(index_file).resolve()

    _MIME = {
        ".html":  "text/html; charset=utf-8",
        ".css":   "text/css",
        ".js":    "application/javascript",
        ".woff":  "font/woff",
        ".woff2": "font/woff2",
        ".png":   "image/png",
        ".jpg":   "image/jpeg",
        ".svg":   "image/svg+xml",
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                      # noqa: N802
            req    = self.path.split("?")[0].lstrip("/")
            target = index if req in ("", "index.html", index.name) else (root / req).resolve()
            if target.is_dir():
                target = target / "index.html"
            if root not in target.parents or not target.is_file():
                self.send_response(404); self.end_headers(); return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",   _MIME.get(target.suffix.lower(), "application/octet-stream"))
            self.send_header("Cache-Control",  "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # noqa: N802
            pass  # silence per-request logs

    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}/"
    print(f"[serve] HTTP server started → {url}")
    return url


# ── Output paths ──────────────────────────────────────────────────────────────

def _page_path(tournament: Tournament, args) -> Path:
    """--output if given, else output/<slug>/index.html."""
    return Path(args.output) if args.output else OUTPUT_DIR / tournament.slug / "index.html"


def _landing_path() -> Path:
    return OUTPUT_DIR / "index.html"


# ── Frozen match cache (finished tournaments) ─────────────────────────────────

def _match_cache_path(tournament: Tournament) -> Path:
    return tournament.cache_dir / "matches.json"


def _load_match_cache(tournament: Tournament) -> tuple | None:
    path = _match_cache_path(tournament)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return (
        {int(day): ms for day, ms in data["days_matches"].items()},
        {int(day): body for day, body in data["bodies_by_day"].items()},
        data["stylesheet_urls"],
    )


def _save_match_cache(tournament: Tournament, days_matches, bodies_by_day, stylesheet_urls) -> None:
    path = _match_cache_path(tournament)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"days_matches": days_matches, "bodies_by_day": bodies_by_day, "stylesheet_urls": stylesheet_urls},
            f, ensure_ascii=False,
        )
    print(f"[cache] Match data frozen -> {path}")


def _no_womens_draw(tournament: Tournament) -> bool:
    """True when the frozen cache says the tournament was played without a women's draw."""
    cached = _load_match_cache(tournament)
    return cached is not None and not any(cached[0].values())


def _get_match_data(tournament: Tournament, force_refresh: bool) -> tuple:
    """Return (days_matches, bodies_by_day, stylesheet_urls).

    A finished tournament is fetched once and frozen in data/cache/<slug>/matches.json;
    later runs make no HTTP request for it (unless --force-refresh). One that had
    matches but no women's draw is frozen empty: no page, and hidden on the landing page.
    """
    frozen = tournament.is_frozen()
    if frozen and not force_refresh:
        cached = _load_match_cache(tournament)
        if cached:
            print(f"[cache] Using frozen match data ({_match_cache_path(tournament)})")
            return cached

    days_matches, bodies_by_day, stylesheet_urls, failed_days, total_matches = scrape_all_days(tournament, gender="Women")
    # No match at all is not frozen: the schedule may be missing or the tournament ID wrong
    if frozen and total_matches and not failed_days:
        _save_match_cache(tournament, days_matches, bodies_by_day, stylesheet_urls)
    elif frozen and failed_days:
        print(f"[cache] Not frozen — day(s) {failed_days} failed; will retry on the next run")
    return days_matches, bodies_by_day, stylesheet_urls


# ── Generation helpers ────────────────────────────────────────────────────────

def _inject_badges(body: str, state: dict) -> str:
    """Rank badges + NEW PAIR badges + emoji (flags, medal, crown) + match stats for one day's widget HTML."""
    if not body:
        return ""
    body = inject_rank_badges(body, state["rankings"], state["ranking_index"])
    body = inject_new_pair_badges(body, state["pair_info"])
    body = inject_emoji(body)
    return inject_match_stats(body, state["stats"].get)


def _display_name(tournament: Tournament) -> str:
    return f"{tournament.name} — Women"


def _write_html(state: dict, args, refresh_interval: int) -> None:
    """Re-generate the HTML file from current state."""
    tournament = state["tournament"]
    generate_html(
        state["bodies_by_day"],
        tournament.dates,
        state["stylesheet_urls"],
        output_path=str(_page_path(tournament, args)),
        tournament_name=_display_name(tournament),
        refresh_interval=refresh_interval,
        timezone_name=tournament.timezone,
        # Standard layout: fonts and theme.css are shared, and the page links back to the landing page
        fonts_dir=None if args.output else OUTPUT_DIR / "fonts",
        assets_dir=None if args.output else OUTPUT_DIR / "assets",
        # … on the season and month this tournament is listed under
        back_href=None if args.output else f"../index.html#{tournament.year}-{tournament.start_date.month:02d}",
        header={
            "name":     tournament.name,
            "year":     tournament.year,
            "tier":     tournament.tier,
            "dates":    _format_dates(tournament.start_date, tournament.end_date),
            "location": tournament.location,
            "image":    tournament.image_url,
        },
    )


def _format_dates(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {end:%b %Y}"
    return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"


def _write_landing() -> None:
    """Write output/index.html: every tournament with name, dates, tier and status."""
    by_slug = {t.slug: t for t in load_tournaments()}
    rows = []
    for entry in load_entries():
        slug = entry["slug"]
        tournament = by_slug.get(slug)
        has_page = (OUTPUT_DIR / slug / "index.html").exists()
        if tournament:
            status = tournament.status()
            dates = _format_dates(tournament.start_date, tournament.end_date)
            if status == "finished" and not has_page:
                if _no_womens_draw(tournament):
                    continue   # men only: not listed
                status = "no data"
        else:
            status = entry.get("status") or "unknown"   # e.g. postponed
            dates = None
        rows.append({
            "name":   entry.get("name") or slug,
            "year":   entry["year"],
            "tier":   entry.get("tier"),
            "dates":  dates,
            "start":  tournament.start_date if tournament else None,
            "status": status.capitalize(),
            "href":   f"{slug}/index.html" if has_page else None,
            "image":  entry.get("image_url"),
        })
    # A season is listed once it has something to show: a page, or a tournament still to be played
    listed = {r["year"] for r in rows if r["href"] or r["status"] in ("Ongoing", "Upcoming")}
    generate_landing_html([r for r in rows if r["year"] in listed], str(_landing_path()))


def _initial_generation(args, tournament: Tournament, force_refresh: bool = False) -> dict | None:
    """Fetch ALL tournament days, load rankings, inject badges, write HTML.

    Returns a state dict for the watch loop, or None on failure.
    """
    output = _page_path(tournament, args)
    print("")
    print("=" * 56)
    print("  FIP Order of Play Generator")
    print(f"  Tournament : {_display_name(tournament)}")
    print(f"  Output     : {output}")
    print("=" * 56)
    print("")

    # Step 1 — fetch all days
    print("-- Step 1: Fetching match data (up to today) -----------")
    days_matches, bodies_by_day, stylesheet_urls = _get_match_data(tournament, force_refresh)
    total_matches  = sum(len(ms) for ms in days_matches.values())
    days_with_data = sum(1 for ms in days_matches.values() if ms)
    if total_matches == 0:
        if tournament.status() == "upcoming":
            print("\n[info] Schedule not published yet — no page generated.")
            return None
        if tournament.status() == "ongoing":
            print("\n[info] No women's matches yet — no page generated.")
            return None
        if _no_womens_draw(tournament):
            print("\n[info] No women's draw at this tournament — no page generated.")
            return None
        print("\n[ERROR] No matches found. Check tournament ID or network.")
        return None
    print(f"         {total_matches} match(es) across {days_with_data} day(s)")
    print(f"         {len(stylesheet_urls)} widget stylesheet(s) collected")
    print("")

    # Step 2 — rankings
    print("-- Step 2: Loading rankings from entry list PDF --------")
    rankings      = get_rankings_from_pdf(
        tournament.entry_list_pdf_url,
        cache_path=tournament.cache_dir / "entry_list.json",
        local_pdf_path=tournament.local_pdf_path,
        force_refresh=force_refresh,
        frozen=tournament.is_frozen(),
    )
    ranking_index = build_lookup_index(rankings)
    print(f"         {len(rankings)} players in rankings cache")
    print("")

    # Step 3 — match stats (finished matches come from data/cache/<slug>/stats.json)
    print("-- Step 3: Loading match stats -------------------------")
    stats = MatchStats(tournament, use_cache=not force_refresh)
    requested = stats.update(bodies_by_day.values())
    print(f"         {requested} match(es) fetched, the rest from cache")
    print("")

    # Step 4 — inject badges for every day
    print("-- Step 4: Injecting rank badges into widget HTML ------")
    state = {
        "stats":           stats,
        "tournament":      tournament,
        "stylesheet_urls": stylesheet_urls,
        "rankings":        rankings,
        "ranking_index":   ranking_index,
        "pair_info":       new_pair_checker(load_partnerships(), tournament.slug),
        "days_matches":    days_matches,
    }
    state["bodies_by_day"] = {day: _inject_badges(body, state) for day, body in bodies_by_day.items()}
    new_pairs = len(state["pair_info"].found)
    print(f"         Rank badges injected for {sum(1 for b in state['bodies_by_day'].values() if b)} day(s)")
    print(f"         {new_pairs} new pair(s) flagged")
    print("")

    # Step 5 — write HTML
    print("-- Step 5: Generating HTML -----------------------------")
    _write_html(state, args, refresh_interval=WATCH_INTERVAL)

    print("")
    print("=" * 56)
    print(f"  Done!  ->  {output.resolve()}")
    print("=" * 56)
    print("")
    return state


def _watch_update(args, state: dict) -> int:
    """Fetch only today's day, update state in-place, rewrite HTML.

    Returns the next sleep interval (LIVE_INTERVAL or WATCH_INTERVAL).
    """
    tournament = state["tournament"]
    today_day = get_today_day(tournament)
    print(f"[watch] {tournament.slug}: updating day {today_day}...")

    matches, body = fetch_one_day(tournament, today_day, gender="Women")

    # Update only today's entry (stats: live matches again, finished ones once)
    state["stats"].update([body])
    state["bodies_by_day"][today_day] = _inject_badges(body, state)
    state["days_matches"][today_day] = matches

    # Detect live match
    has_live = any(m.get("status") == "in_progress" for m in matches)
    interval = LIVE_INTERVAL if has_live else WATCH_INTERVAL

    _write_html(state, args, refresh_interval=interval)

    status = "🔴 LIVE — next update in 15 s" if has_live else f"next update in {interval} s"
    print(f"[watch] Day {today_day} updated — {status}")
    return interval


def _watch_cycle(args, tournaments: list[Tournament], states: dict[str, dict]) -> int:
    """Refresh every tournament still being played. Finished/upcoming ones are left alone.

    Returns the next sleep interval.
    """
    interval = WATCH_INTERVAL
    for tournament in tournaments:
        if not tournament.is_refreshable():
            continue
        try:
            state = states.get(tournament.slug)
            if state:
                interval = min(interval, _watch_update(args, state))
            else:
                # Schedule was not published yet at the previous attempt
                state = _initial_generation(args, tournament)
                if state:
                    states[tournament.slug] = state
                    if not args.output:
                        _write_landing()
        except Exception as exc:
            print(f"[watch] ERROR {tournament.slug} (will retry): {exc}")
    return interval


# ── Entry point ───────────────────────────────────────────────────────────────

def _season_tournaments(year: str | None) -> list[Tournament]:
    """Tournaments for --all: one season (default: this year's, else the newest), or every season for 'all'."""
    tournaments = load_tournaments()
    if year == "all":
        return tournaments
    seasons = {t.year for t in tournaments}
    if not seasons:
        raise ValueError(f"No usable tournaments in {TOURNAMENTS_PATH}")
    this_year = date.today().year
    season = int(year) if year else (this_year if this_year in seasons else max(seasons))
    if season not in seasons:
        raise ValueError(f"No tournaments for season {season}. Available: {', '.join(map(str, sorted(seasons)))}")
    return [t for t in tournaments if t.year == season]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a styled multi-day HTML Order of Play page from FIP live data."
    )
    parser.add_argument("--tournament", metavar="SLUG",
        help="Tournament slug from data/tournaments.json, e.g. buenos-aires-p1-2026 "
             "(default: the tournament being played today, else the most recent one)")
    parser.add_argument("--all", action="store_true",
        help="Generate every tournament of the current season plus the landing page")
    parser.add_argument("--year", metavar="YEAR",
        help="With --all: the season to generate instead, e.g. 2024, or 'all' for every season")
    parser.add_argument("--output", metavar="FILE",
        help="Write a single tournament page to FILE instead of output/<slug>/index.html")
    parser.add_argument("--force-refresh", action="store_true",
        help="Re-download entry list PDFs and re-fetch finished tournaments, ignoring caches")
    parser.add_argument("--open",  action="store_true",
        help="Open the generated HTML in the default browser")
    parser.add_argument("--watch", action="store_true",
        help="Keep running: update only today's day of the ongoing tournament on each cycle (combine with --open)")
    parser.add_argument("--serve", action="store_true",
        help=f"Serve via http://localhost:{SERVE_PORT}/ with no-cache headers (combine with --watch --open)")
    args = parser.parse_args()

    if args.all and (args.tournament or args.output):
        parser.error("--all cannot be combined with --tournament or --output")
    if args.year and not args.all:
        parser.error("--year needs --all")
    if args.year and args.year != "all" and not args.year.isdigit():
        parser.error("--year takes a season (e.g. 2024) or 'all'")

    try:
        if args.all:
            tournaments = _season_tournaments(args.year)
        else:
            tournaments = [get_tournament(args.tournament)] if args.tournament else default_candidates()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"[ERROR] {exc.args[0]}")
        sys.exit(1)

    # Initial full generation
    states: dict[str, dict] = {}
    for tournament in tournaments:
        try:
            state = _initial_generation(args, tournament, force_refresh=args.force_refresh)
        except Exception as exc:
            if not args.all:
                raise
            print(f"[ERROR] {tournament.slug}: {exc}")   # one broken tournament must not stop the rest
            state = None
        if state:
            states[tournament.slug] = state
            if not args.all:
                tournaments = [tournament]   # default pick: the first candidate with matches
                break
    if not args.output:
        _write_landing()
    if not args.all and not states:
        sys.exit(1)

    # Start HTTP server before opening browser (if requested)
    page = _landing_path() if args.all else _page_path(tournaments[0], args)
    open_url = str(page.resolve())
    if args.serve:
        if args.output:
            open_url = _start_server(page.parent, page)
        else:
            open_url = _start_server(OUTPUT_DIR, _landing_path())
            if not args.all:
                open_url += f"{tournaments[0].slug}/"

    if args.open:
        _open_browser(open_url)

    if not args.watch:
        if args.serve:
            print("[serve] Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                print("\n[serve] Stopped.")
        return

    # Watch loop — only today's day of a tournament still being played is re-fetched each cycle
    interval = WATCH_INTERVAL
    print(f"[watch] Live mode active — press Ctrl+C to stop.")
    if not any(t.is_refreshable() for t in tournaments):
        print("[watch] No ongoing tournament — nothing to refresh.")
    while True:
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")
            break
        try:
            interval = _watch_cycle(args, tournaments, states)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")
            break


if __name__ == "__main__":
    main()
