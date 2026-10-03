"""
FIP Order of Play Generator -- entry point.

Usage:
  python main.py --serve --watch --open   # recommended live mode
  python main.py --watch --open           # live mode via file://
  python main.py                          # single run
  python main.py --force-refresh --open
  python main.py --tournament buenos-aires-p1-2026   # pick a tournament from data/tournaments.json
"""

import argparse
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scrape_matches import scrape_all_days, fetch_one_day, get_today_day
from scrape_rankings import get_rankings_from_pdf, build_lookup_index
from generate_html import generate_html, inject_rank_badges
from tournaments import Tournament, default_tournament, get_tournament

DEFAULT_OUTPUT     = "output/index.html"
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

def _start_server(output_path: str, port: int = SERVE_PORT) -> str:
    """Serve the output directory over HTTP with Cache-Control: no-store.

    Returns the URL to open in the browser.
    Runs in a daemon thread so it exits when the main process ends.
    """
    out     = Path(output_path).resolve()
    out_dir = out.parent

    _MIME = {
        ".html":  "text/html; charset=utf-8",
        ".css":   "text/css",
        ".js":    "application/javascript",
        ".woff":  "font/woff",
        ".woff2": "font/woff2",
        ".png":   "image/png",
        ".jpg":   "image/jpeg",
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                      # noqa: N802
            req    = self.path.split("?")[0].lstrip("/")
            target = out if req in ("", "index.html", out.name) else out_dir / req
            if not target.exists() or not target.is_file():
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


# ── Generation helpers ────────────────────────────────────────────────────────

def _display_name(tournament: Tournament) -> str:
    return f"{tournament.name} — Women"


def _write_html(state: dict, args, refresh_interval: int) -> None:
    """Re-generate the HTML file from current state."""
    tournament = state["tournament"]
    generate_html(
        state["bodies_by_day"],
        tournament.dates,
        state["stylesheet_urls"],
        output_path=args.output,
        tournament_name=_display_name(tournament),
        refresh_interval=refresh_interval,
    )


def _initial_generation(args, tournament: Tournament, force_refresh: bool = False) -> dict | None:
    """Fetch ALL tournament days, load rankings, inject badges, write HTML.

    Returns a state dict for the watch loop, or None on failure.
    """
    print("")
    print("=" * 56)
    print("  FIP Order of Play Generator")
    print(f"  Tournament : {_display_name(tournament)}")
    print(f"  Output     : {args.output}")
    print("=" * 56)
    print("")

    # Step 1 — fetch all days
    print("-- Step 1: Fetching match data (up to today) -----------")
    days_matches, bodies_by_day, stylesheet_urls = scrape_all_days(tournament, gender="Women")
    total_matches  = sum(len(ms) for ms in days_matches.values())
    days_with_data = sum(1 for ms in days_matches.values() if ms)
    if total_matches == 0:
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
    )
    ranking_index = build_lookup_index(rankings)
    print(f"         {len(rankings)} players in rankings cache")
    print("")

    # Step 3 — inject badges for every day
    print("-- Step 3: Injecting rank badges into widget HTML ------")
    enriched_bodies: dict[int, str] = {
        day: (inject_rank_badges(body, rankings, ranking_index) if body else "")
        for day, body in bodies_by_day.items()
    }
    print(f"         Rank badges injected for {sum(1 for b in enriched_bodies.values() if b)} day(s)")
    print("")

    # Step 4 — write HTML
    print("-- Step 4: Generating HTML -----------------------------")
    state = {
        "tournament":      tournament,
        "bodies_by_day":  enriched_bodies,
        "stylesheet_urls": stylesheet_urls,
        "rankings":        rankings,
        "ranking_index":   ranking_index,
        "days_matches":    days_matches,
    }
    _write_html(state, args, refresh_interval=WATCH_INTERVAL)

    print("")
    print("=" * 56)
    print(f"  Done!  ->  {Path(args.output).resolve()}")
    print("=" * 56)
    print("")
    return state


def _watch_update(args, state: dict) -> int:
    """Fetch only today's day, update state in-place, rewrite HTML.

    Returns the next sleep interval (LIVE_INTERVAL or WATCH_INTERVAL).
    """
    tournament = state["tournament"]
    today_day = get_today_day(tournament)
    print(f"[watch] Updating day {today_day}...")

    matches, body = fetch_one_day(tournament, today_day, gender="Women")

    # Update only today's entry
    state["bodies_by_day"][today_day] = (
        inject_rank_badges(body, state["rankings"], state["ranking_index"])
        if body else ""
    )
    state["days_matches"][today_day] = matches

    # Detect live match
    has_live = any(m.get("status") == "in_progress" for m in matches)
    interval = LIVE_INTERVAL if has_live else WATCH_INTERVAL

    _write_html(state, args, refresh_interval=interval)

    status = "🔴 LIVE — next update in 15 s" if has_live else f"next update in {interval} s"
    print(f"[watch] Day {today_day} updated — {status}")
    return interval


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a styled multi-day HTML Order of Play page from FIP live data."
    )
    parser.add_argument("--tournament", metavar="SLUG",
        help="Tournament slug from data/tournaments.json, e.g. buenos-aires-p1-2026 "
             "(default: the tournament being played today, else the most recent one)")
    parser.add_argument("--output",     default=DEFAULT_OUTPUT)
    parser.add_argument("--force-refresh", action="store_true",
        help="Re-download the entry list PDF even if the 24-hour cache is fresh")
    parser.add_argument("--open",  action="store_true",
        help="Open the generated HTML in the default browser")
    parser.add_argument("--watch", action="store_true",
        help="Keep running: update only today's day on each cycle (combine with --open)")
    parser.add_argument("--serve", action="store_true",
        help=f"Serve via http://localhost:{SERVE_PORT}/ with no-cache headers (combine with --watch --open)")
    args = parser.parse_args()

    try:
        tournament = get_tournament(args.tournament) if args.tournament else default_tournament()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"[ERROR] {exc.args[0]}")
        sys.exit(1)

    # Initial full generation
    state = _initial_generation(args, tournament, force_refresh=args.force_refresh)
    if state is None:
        sys.exit(1)

    # Start HTTP server before opening browser (if requested)
    open_url = str(Path(args.output).resolve())
    if args.serve:
        open_url = _start_server(args.output)

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

    # Watch loop — only today's day is re-fetched each cycle
    interval = WATCH_INTERVAL
    print(f"[watch] Live mode active — press Ctrl+C to stop.")
    while True:
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")
            break
        try:
            interval = _watch_update(args, state)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")
            break
        except Exception as exc:
            print(f"[watch] ERROR (will retry in {interval}s): {exc}")


if __name__ == "__main__":
    main()
