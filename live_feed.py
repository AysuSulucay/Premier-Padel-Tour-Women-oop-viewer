"""Today's day of a tournament being played, as JSON for the page's live refresh.

Served at ``/api/live?t=<slug>`` (api/live.py on Vercel). The badge data comes from the
deployed files only — nothing is downloaded or written there; the only requests go to the
widget: today's schedule, and the stats of today's matches.

The answer says when to ask again (``next_at``), and a cache may keep it until then:
    not a tournament day          → no request to the widget
    before the first match        → 10 min before its start time
    waiting for a "Followed by"   → every 2 min
    waiting for a "Not before"    → at that time
    a women's match is live       → every 15 s
    the women's day is over       → the next day
"""

import json
import re
import time
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from generate_html import inject_badges, panel_content
from partnerships import PARTNERSHIPS_PATH, new_pair_checker
from scrape_matches import fetch_day
from scrape_rankings import add_profile_urls, build_lookup_index
from scrape_stats import MatchStats
from tournaments import Tournament, load_tournaments

LIVE_SECONDS        = 15     # a women's match is being played
POLL_SECONDS        = 120    # a women's match can start any moment ("Followed by", or its time has come)
START_LEAD_SECONDS  = 600    # wake up this long before a "Starting at" time
NO_SCHEDULE_SECONDS = 600    # today's schedule is not published yet
IDLE_SECONDS        = 3600   # not a tournament day / unknown tournament
ERROR_SECONDS       = 15     # the widget did not answer
DAY_ROLLOVER_HOUR   = 4      # venue time: a night session that runs past midnight is still "today"


def badge_state(
    tournament: Tournament, rankings: dict, partnerships: list[dict], stats: MatchStats,
    force_refresh: bool = False, offline: bool = False,
) -> dict:
    """What ``generate_html.inject_badges`` needs for one tournament's cards.

    Sets the players' ``profile_url`` in *rankings* (``offline``: from data/fip_profiles.json as it is).
    """
    # Names as other entry lists spell them: the widget can show more surnames than this list
    long_names = {
        row[key] for row in partnerships if row["tournament_slug"] == tournament.slug
        for key in ("player_name", "partner_name")
    }
    # Badge links: the players' padelfip.com profiles
    known_names = {row[key] for row in partnerships for key in ("player_name", "partner_name")}
    add_profile_urls(rankings, long_names, known_names, force_refresh=force_refresh, offline=offline)
    return {
        "tournament":    tournament,
        "rankings":      rankings,
        "ranking_index": build_lookup_index(rankings, long_names),
        "pair_info":     new_pair_checker(partnerships, tournament.slug),
        "stats":         stats,
    }


# ── When is the next change due? ──────────────────────────────────────────────

def _play_date(now: datetime):
    return (now - timedelta(hours=DAY_ROLLOVER_HOUR)).date()


def tournament_day(tournament: Tournament, now: datetime) -> int | None:
    """Day number being played at *now* (venue time); None outside the tournament."""
    day = (_play_date(now) - tournament.start_date).days + 1
    return day if 1 <= day <= tournament.total_days else None


_CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)?", re.I)


def _named_time(time_note: str, now: datetime) -> datetime | None:
    """'Starting at 9:00 AM' / 'Not before 14:30' → that time today; None for 'Followed by'."""
    m = _CLOCK_RE.search(time_note)
    if not m:
        return None
    hour = int(m[1])
    if m[3]:
        hour = hour % 12 + (12 if m[3].upper() == "PM" else 0)
    return datetime.combine(_play_date(now), clock_time(hour % 24, int(m[2])), tzinfo=now.tzinfo)


def next_check(matches: list[dict], now: datetime, gender: str = "Women") -> int | None:
    """Seconds until the *gender* cards of the day can change; None once her last match is over.

    *matches*: the day's matches of every category in court and slot order (``parse_widget``) —
    a women's "Followed by" match waits for the men's match before it. A match cannot start
    before its named time, nor before the match ahead of it on the same court.
    """
    waits = []
    court, free_at = None, now
    for match in matches:
        if match["court"] != court:
            court, free_at = match["court"], now   # when this court's next match can start
        if match["status"] != "upcoming":
            free_at = now   # over, or being played: the next one can follow any moment
            if match["status"] == "in_progress" and match["category"] == gender:
                waits.append(LIVE_SECONDS)
            continue
        named = _named_time(match["time_note"], now)
        free_at = max(free_at, named) if named else free_at
        if match["category"] == gender:
            lead = START_LEAD_SECONDS if match["time_note"].lower().startswith("starting") else 0
            waits.append(max(POLL_SECONDS, int((free_at - now).total_seconds()) - lead))
    return min(waits) if waits else None


# ── The answer ────────────────────────────────────────────────────────────────

# Kept while the server process lives. On Vercel that is one warm instance: a new one reads
# the deployed files again and fetches the stats of today's finished matches once more.
_states: dict[str, dict] = {}
_answers: dict[str, tuple[float, int, dict]] = {}   # slug → (valid until, status, body)


def _state(tournament: Tournament) -> dict:
    if tournament.slug not in _states:
        entry_list = tournament.cache_dir / "entry_list.json"
        rankings = json.loads(entry_list.read_text(encoding="utf-8")) if entry_list.exists() else {}
        partnerships = (
            json.loads(PARTNERSHIPS_PATH.read_text(encoding="utf-8")) if PARTNERSHIPS_PATH.exists() else []
        )
        _states[tournament.slug] = badge_state(
            tournament, rankings, partnerships, MatchStats(tournament, persist=False), offline=True,
        )
    return _states[tournament.slug]


def live_payload(tournament: Tournament, now: datetime) -> tuple[dict, int]:
    """(``{day, html, next_at}``, seconds it stays valid) for *now* in venue time.

    ``html`` is the inner HTML of the day panel ``#fip-day-<day>``; ``next_at`` (epoch
    seconds) is when to ask again. Outside the tournament: ``{day: None, next_at: None}``.
    """
    day = tournament_day(tournament, now)
    if day is None:
        return {"day": None, "next_at": None}, IDLE_SECONDS
    try:
        matches, body = fetch_day(tournament, day)
    except Exception as exc:
        if "404" not in str(exc):
            raise
        matches, body = [], ""   # that day is not published yet
    state = _state(tournament)
    state["stats"].update([body])

    if not matches:
        seconds = NO_SCHEDULE_SECONDS
    else:
        seconds = next_check(matches, now)
        if seconds is None:   # the day is over: nothing changes before the next one
            rollover = datetime.combine(
                _play_date(now) + timedelta(days=1), clock_time(DAY_ROLLOVER_HOUR), tzinfo=now.tzinfo,
            )
            seconds = max(POLL_SECONDS, int((rollover - now).total_seconds()))
        elif not tournament.timezone:
            seconds = min(seconds, POLL_SECONDS)   # venue time unknown: the named times cannot be trusted
    payload = {
        "day":     day,
        "html":    panel_content(inject_badges(body, state)),
        "next_at": int(now.timestamp()) + seconds,
    }
    return payload, seconds


def answer(slug: str) -> tuple[int, dict, int]:
    """(HTTP status, JSON body, seconds a cache may keep it) for ``/api/live?t=<slug>``.

    An answer is reused until it runs out, whatever else the request carries — the widget
    gets one request per interval however the endpoint is called.
    """
    known = _answers.get(slug)
    if known and known[0] > time.time():
        return known[1], known[2], max(1, int(known[0] - time.time()))
    tournament = next((t for t in load_tournaments() if t.slug == slug), None)
    if tournament is None:
        return 404, {"error": "unknown tournament"}, IDLE_SECONDS
    try:
        body, seconds = live_payload(tournament, datetime.now(ZoneInfo(tournament.timezone or "UTC")))
        status = 200
    except Exception as exc:
        print(f"[live] {slug}: {exc!r}")
        status, body, seconds = 502, {"error": "schedule unavailable"}, ERROR_SECONDS
    _answers[slug] = (time.time() + seconds, status, body)
    return status, body, seconds
