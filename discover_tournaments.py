"""
Discover every Premier Padel tournament of a season on padelfip.com.

Usage:
    python discover_tournaments.py --year 2026
    python discover_tournaments.py --year 2026 --only buenos-aires-p1-2026
    python discover_tournaments.py --year 2026 --overwrite

Writes data/tournaments.json and downloads each women's entry list PDF to
data/pdfs/<slug>/entry_list_women.pdf.
"""

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from unidecode import unidecode

BASE_URL = "https://www.padelfip.com"
CALENDAR_URL = BASE_URL + "/calendar-premier-padel/?events-year={year}"
AJAX_URL = BASE_URL + "/wp-admin/admin-ajax.php"

DATA_DIR = Path(__file__).parent / "data"
TOURNAMENTS_PATH = DATA_DIR / "tournaments.json"
PDF_DIR = DATA_DIR / "pdfs"
PDF_NAME = "entry_list_women.pdf"

REQUEST_DELAY_SEC = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Premier Padel events: category-event-fip-ppt-p1 / -p2 / -major, and
# category-event-fip-pp-master-finals for the season finals.
_CATEGORY_RE = re.compile(r"^category-event-fip-ppt?-(.+)$")
_ID_RE = re.compile(r"^idEvent_(\d+)$")
_NONCE_RE = re.compile(r"var\s+padelfip_ajax\s*=\s*(\{.*?\});")
_PDF_MAP_RE = re.compile(r"var\s+pdfMap\s*=\s*(\{.*?\});", re.S)
_OOP_URL_RE = re.compile(r'fetchUrl:\s*("[^"]*get-oop-data\.php[^"]*")')
_DAY_MONTH_RE = re.compile(r"(\d{1,2})(?:\s*([A-Za-z]{3,}))?")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_HEADER_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
)}
_TIER_NAMES = {"major": "Major", "master-finals": "Finals", "finals": "Finals"}

# IANA time zone of the venue, from the event's "Location" ('Buenos aires - Argentina').
# A city entry wins over its country; countries with several zones are listed by city only.
_CITY_TIMEZONES = {
    "miami": "America/New_York",
    "new york": "America/New_York",
    "cancun": "America/Cancun",
    "acapulco": "America/Mexico_City",
    "mexico city": "America/Mexico_City",
    "monterrey": "America/Monterrey",
}
_COUNTRY_TIMEZONES = {
    "argentina": "America/Argentina/Buenos_Aires",
    "paraguay": "America/Asuncion",
    "chile": "America/Santiago",
    "venezuela": "America/Caracas",
    "spain": "Europe/Madrid",
    "italy": "Europe/Rome",
    "france": "Europe/Paris",
    "belgium": "Europe/Brussels",
    "netherlands": "Europe/Amsterdam",
    "germany": "Europe/Berlin",
    "united kingdom": "Europe/London",
    "great britain": "Europe/London",
    "england": "Europe/London",
    "portugal": "Europe/Lisbon",
    "sweden": "Europe/Stockholm",
    "finland": "Europe/Helsinki",
    "egypt": "Africa/Cairo",
    "south africa": "Africa/Johannesburg",
    "saudi arabia": "Asia/Riyadh",
    "qatar": "Asia/Qatar",
    "kuwait": "Asia/Kuwait",
    "bahrain": "Asia/Bahrain",
    "united arab emirates": "Asia/Dubai",
    "uae": "Asia/Dubai",
}

# Fields that are always recomputed, even when the entry already exists.
_ALWAYS_REFRESH = {"status"}

_last_request_at = 0.0


def _warn(slug: str, msg: str) -> None:
    print(f"  [WARN] {slug}: {msg}")


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """session.request with a 1 s gap between consecutive requests."""
    global _last_request_at
    wait = REQUEST_DELAY_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    try:
        return session.request(method, url, timeout=60, **kwargs)
    finally:
        _last_request_at = time.monotonic()


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def fetch_event_urls(session: requests.Session, year: int) -> list[str]:
    """Return unique event URLs from the Premier Padel calendar, in page order."""
    resp = _request(session, "GET", CALENDAR_URL.format(year=year))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        parsed = urlparse(a["href"])
        if not re.fullmatch(r"/events/[^/]+/?", parsed.path):
            continue
        url = f"{BASE_URL}{parsed.path.rstrip('/')}/"
        if url not in urls:
            urls.append(url)
    return urls


def _slug_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Event page
# ---------------------------------------------------------------------------

def _parse_id(raw: str) -> int | str:
    """Widget IDs are numeric, but a leading zero is significant ('0905' != '905') → keep it as a string."""
    return int(raw) if str(int(raw)) == raw else raw


def _parse_classes(soup: BeautifulSoup) -> tuple[str | None, int | str | None]:
    """Read tier and tournament ID from the `event category-event-… idEvent_…` element."""
    tier = tournament_id = None
    el = soup.find(class_=_ID_RE) or soup.find(class_=_CATEGORY_RE)
    for cls in (el.get("class", []) if el else []):
        if m := _CATEGORY_RE.match(cls):
            suffix = m.group(1)
            tier = _TIER_NAMES.get(suffix, suffix.upper())
        elif m := _ID_RE.match(cls):
            tournament_id = _parse_id(m.group(1))
    return tier, tournament_id


def _make_name(raw: str, year: int) -> str:
    """'BUENOS AIRES P1' → 'Premier Padel Buenos Aires P1 2026'."""
    words = [w if re.fullmatch(r"P\d", w, re.I) else w.capitalize() for w in raw.split()]
    name = " ".join(words)
    name = re.sub(r"\bP(\d)\b", r"P\1", name, flags=re.I)
    if "premier padel" not in name.lower():
        name = f"Premier Padel {name}"
    if not _YEAR_RE.search(name):
        name = f"{name} {year}"
    return name


def _timezone_for(location: str | None) -> str | None:
    """'Buenos aires - Argentina' → 'America/Argentina/Buenos_Aires' (None if the venue is unknown)."""
    if not location:
        return None
    parts = [unidecode(p).lower().strip() for p in location.split(" - ")]
    for part in parts:
        if part in _CITY_TIMEZONES:
            return _CITY_TIMEZONES[part]
    return _COUNTRY_TIMEZONES.get(parts[-1])


def _overview_text(soup: BeautifulSoup, title: str) -> str | None:
    """Text of the p.overview__text that follows span.overview__title == title."""
    for span in soup.find_all("span", class_="overview__title"):
        if span.get_text(strip=True).lower() == title.lower():
            text_el = span.find_next_sibling(class_="overview__text")
            if text_el:
                return " ".join(text_el.get_text(" ", strip=True).split()) or None
    return None


def _parse_range(text: str, default_year: int) -> tuple[date, date] | None:
    """
    Parse 'Sunday 10 May – Tuesday 12 May 2026' (also '12–17 May 2026').
    The year appears only at the end; a day without a month takes the next one.
    """
    years = _YEAR_RE.findall(text)
    year = int(years[-1]) if years else default_year
    pairs = []
    for day, month in _DAY_MONTH_RE.findall(_YEAR_RE.sub(" ", text)):
        pairs.append([int(day), _MONTHS.get(month[:3].lower()) if month else None])
    for i in range(len(pairs) - 2, -1, -1):
        if pairs[i][1] is None:
            pairs[i][1] = pairs[i + 1][1]
    pairs = [p for p in pairs if p[1]]
    if not pairs:
        return None
    (d1, m1), (d2, m2) = pairs[0], pairs[-1]
    try:
        end = date(year, m2, d2)
        start = date(year - 1 if m1 > m2 else year, m1, d1)
    except ValueError:
        return None
    return start, end


def _parse_header_dates(soup: BeautifulSoup) -> tuple[date, date] | None:
    """Header range: <div class="event__date">31/05/2026 - 07/06/2026</div> ('POSTPONED' → None)."""
    el = soup.find(class_="event__date")
    found = _HEADER_DATE_RE.findall(el.get_text(" ", strip=True)) if el else []
    if len(found) != 2:
        return None
    try:
        start, end = (date(int(y), int(m), int(d)) for d, m, y in found)
    except ValueError:
        return None
    return start, end


def _parse_dates(
    soup: BeautifulSoup, year: int, slug: str, totalday: int | None
) -> tuple[date | None, date | None]:
    """
    start = Qualification start (or Main draw start), end = Main draw end.
    The header range is the fallback, and wins when only it agrees with the
    OOP widget's totalday (e.g. the Qualification block is missing).
    """
    quali_text = _overview_text(soup, "Qualification")
    main_text = _overview_text(soup, "Main draw")
    quali = _parse_range(quali_text, year) if quali_text else None
    main = _parse_range(main_text, year) if main_text else None
    if quali_text and not quali:
        _warn(slug, f"could not parse Qualification dates: {quali_text!r}")
    if main_text and not main:
        _warn(slug, f"could not parse Main draw dates: {main_text!r}")

    overview = ((quali or main)[0], main[1]) if main else None
    header = _parse_header_dates(soup)
    if not overview or not header or overview == header:
        return overview or header or (None, None)

    def days(r):
        return (r[1] - r[0]).days + 1

    chosen = header if totalday and days(header) == totalday != days(overview) else overview
    _warn(
        slug,
        f"overview dates {overview[0]}..{overview[1]} differ from header {header[0]}..{header[1]}"
        f" (OOP totalday={totalday}); using {'header' if chosen is header else 'overview'}",
    )
    return chosen


def _parse_oop_params(html: str) -> dict:
    """id / totalday from the OOP widget's fetchUrl (JSON-escaped in the page source)."""
    m = _OOP_URL_RE.search(html)
    if not m:
        return {}
    try:
        query = parse_qs(urlparse(json.loads(m.group(1))).query)
    except ValueError:
        return {}
    params = {k: v[0] for k, v in query.items() if k in ("id", "totalday") and v[0].isdigit()}
    if "id" in params:
        params["id"] = _parse_id(params["id"])
    if "totalday" in params:
        params["totalday"] = int(params["totalday"])
    return params


def _is_postponed(soup: BeautifulSoup) -> bool:
    """The header shows 'POSTPONED' in place of the date range."""
    el = soup.find(class_="event__date")
    return bool(el) and "postponed" in el.get_text(" ", strip=True).lower()


def _status(start: date | None, end: date | None, today: date, postponed: bool = False) -> str | None:
    if postponed:
        return "postponed"
    if not start or not end:
        return None
    if today > end:
        return "finished"
    if today < start:
        return "upcoming"
    return "ongoing"


# ---------------------------------------------------------------------------
# Entry list (admin-ajax.php → load_entrylist_tab)
# ---------------------------------------------------------------------------

def _refresh_nonce(session: requests.Session) -> str | None:
    resp = _request(session, "POST", AJAX_URL, data={"action": "padelfip_refresh_nonce"})
    try:
        return resp.json()["data"]["nonce"]
    except (ValueError, KeyError, TypeError):
        return None


def fetch_entry_list_pdf_url(
    session: requests.Session, html: str, soup: BeautifulSoup, event_url: str, slug: str
) -> str | None:
    """
    The Player List tab is loaded by JS. The response HTML carries an inline
    `var pdfMap = {"M": {"": url, …}, "W": {"": url, …}}` — women's PDF is pdfMap.W[""].
    """
    container = soup.find(id="entrylist-ajax-container")
    post_id = container.get("data-post-id") if container else None
    if not post_id:
        _warn(slug, "no entry list tab on event page")
        return None

    nonce = None
    if m := _NONCE_RE.search(html):
        try:
            nonce = json.loads(m.group(1)).get("nonce")
        except ValueError:
            pass

    resp = None
    for attempt in range(2):
        if nonce:
            resp = _request(
                session, "POST", AJAX_URL,
                data={"action": "load_entrylist_tab", "security": nonce, "post_id": post_id},
                headers={"Referer": event_url, "X-Requested-With": "XMLHttpRequest"},
            )
            if resp.status_code != 403:
                break
        # Page HTML is served from cache, so its nonce may be stale → refresh once.
        if attempt == 0:
            nonce = _refresh_nonce(session)
    if resp is None or resp.status_code != 200:
        _warn(slug, f"entry list request failed (HTTP {resp.status_code if resp is not None else 'no nonce'})")
        return None

    try:
        payload = resp.json()
        tab_html = payload["data"]["html"]
    except (ValueError, KeyError, TypeError):
        _warn(slug, "entry list not available yet")
        return None

    m = _PDF_MAP_RE.search(tab_html)
    if not m:
        _warn(slug, "pdfMap not found in entry list response")
        return None
    try:
        women = json.loads(m.group(1)).get("W") or {}
    except ValueError:
        _warn(slug, "pdfMap is not valid JSON")
        return None
    url = women.get("") if isinstance(women, dict) else None
    if not url:
        _warn(slug, "no women's entry list PDF")
        return None
    return url


def download_pdf(session: requests.Session, url: str, slug: str, force: bool) -> bool:
    """Download the PDF to data/pdfs/<slug>/entry_list_women.pdf. Returns True if a local copy exists."""
    path = PDF_DIR / slug / PDF_NAME
    if path.exists() and not force:
        return True
    try:
        resp = _request(session, "GET", url)
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ValueError("response is not a PDF")
    except (requests.RequestException, ValueError) as exc:
        _warn(slug, f"PDF download failed: {exc}")
        return path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return True


# ---------------------------------------------------------------------------
# One tournament
# ---------------------------------------------------------------------------

def discover_event(session: requests.Session, event_url: str, year: int, today: date) -> dict | None:
    """Scrape one event page. Returns None if it is not a Premier Padel event."""
    slug = _slug_from_url(event_url)
    resp = _request(session, "GET", event_url)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    tier, tournament_id = _parse_classes(soup)
    if tier is None:
        print(f"  [SKIP] {slug}: not a Premier Padel event")
        return None

    oop = _parse_oop_params(html)
    if tournament_id is None:
        tournament_id = oop.get("id")
        if tournament_id is None:
            _warn(slug, "tournament ID not found")
    elif "id" in oop and oop["id"] != tournament_id:
        _warn(slug, f"idEvent_{tournament_id} differs from OOP widget id {oop['id']}")

    h1 = soup.find("h1", class_="event__name")
    raw_name = h1.get_text(" ", strip=True) if h1 else None
    if not raw_name:
        _warn(slug, "event name not found")
    name = _make_name(raw_name, year) if raw_name else None

    start, end = _parse_dates(soup, year, slug, oop.get("totalday"))
    total_days = (end - start).days + 1 if start and end else None
    postponed = _is_postponed(soup)
    if total_days is None:
        _warn(slug, "postponed, no dates" if postponed else "start/end dates not found")
        total_days = oop.get("totalday")
    elif "totalday" in oop and oop["totalday"] != total_days:
        _warn(slug, f"computed total_days={total_days} but OOP widget says totalday={oop['totalday']}")

    location = _overview_text(soup, "Location")
    tz_name = _timezone_for(location)
    if tz_name is None:
        _warn(slug, f"no time zone known for location {location!r} — set \"timezone\" in tournaments.json")

    pdf_url = fetch_entry_list_pdf_url(session, html, soup, event_url, slug)

    return {
        "slug": slug,
        "name": name,
        "tier": tier,
        "tournament_id": tournament_id,
        "year": year,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "total_days": total_days,
        "status": _status(start, end, today, postponed),
        "location": location,
        "timezone": tz_name,
        "event_url": event_url,
        "entry_list_pdf_url": pdf_url,
    }


# ---------------------------------------------------------------------------
# tournaments.json
# ---------------------------------------------------------------------------

def load_tournaments() -> list[dict]:
    if not TOURNAMENTS_PATH.exists():
        return []
    with open(TOURNAMENTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def merge_entry(existing: dict | None, scraped: dict, overwrite: bool) -> dict:
    """
    Existing non-null values win, so manual edits survive a re-run; scraped
    values only fill missing/null fields (unless --overwrite).
    """
    if existing is None:
        return scraped
    merged = dict(existing)
    for key, value in scraped.items():
        old = existing.get(key)
        if old is None or key in _ALWAYS_REFRESH or overwrite:
            merged[key] = value if value is not None or key in _ALWAYS_REFRESH else old
        elif value is not None and value != old:
            print(f"  [KEEP] {scraped['slug']}: {key}={old!r} kept (site says {value!r}; use --overwrite to update)")
    return merged


def save_tournaments(tournaments: list[dict]) -> None:
    tournaments.sort(key=lambda t: (t.get("start_date") is None, t.get("start_date") or "", t["slug"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOURNAMENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(tournaments, f, ensure_ascii=False, indent=2)
        f.write("\n")


def print_summary(rows: list[tuple[dict, bool]]) -> None:
    header = ("slug", "tier", "ID", "start", "end", "days", "status", "PDF")
    table = [header] + [
        (
            t["slug"], t.get("tier") or "-", str(t.get("tournament_id") or "-"),
            t.get("start_date") or "-", t.get("end_date") or "-",
            str(t.get("total_days") or "-"), t.get("status") or "-", "yes" if has_pdf else "no",
        )
        for t, has_pdf in rows
    ]
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    print()
    for i, row in enumerate(table):
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Premier Padel tournaments on padelfip.com")
    parser.add_argument("--year", type=int, default=date.today().year, help="Season year (default: current year)")
    parser.add_argument("--only", metavar="SLUG", help="Process a single tournament, e.g. buenos-aires-p1-2026")
    parser.add_argument("--overwrite", action="store_true",
                        help="Let scraped values replace existing ones in tournaments.json and re-download PDFs")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    today = date.today()
    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Fetching {args.year} Premier Padel calendar…")
    event_urls = fetch_event_urls(session, args.year)
    print(f"  {len(event_urls)} events found")
    if args.only:
        event_urls = [u for u in event_urls if _slug_from_url(u) == args.only]
        if not event_urls:
            print(f"  [ERROR] '{args.only}' is not in the {args.year} calendar")
            sys.exit(1)

    by_slug = {t["slug"]: t for t in load_tournaments()}
    rows: list[tuple[dict, bool]] = []

    for event_url in event_urls:
        slug = _slug_from_url(event_url)
        print(f"{slug}")
        try:
            scraped = discover_event(session, event_url, args.year, today)
        except Exception as exc:  # one broken event must not stop the rest
            _warn(slug, f"failed: {exc}")
            continue
        if scraped is None:
            continue

        existing = by_slug.get(slug)
        entry = merge_entry(existing, scraped, args.overwrite)
        by_slug[slug] = entry

        has_pdf = False
        pdf_url = entry.get("entry_list_pdf_url")
        if pdf_url:
            url_changed = existing is not None and existing.get("entry_list_pdf_url") != pdf_url
            has_pdf = download_pdf(session, pdf_url, slug, force=args.overwrite or url_changed)
        elif (PDF_DIR / slug / PDF_NAME).exists():
            has_pdf = True
        rows.append((entry, has_pdf))

    save_tournaments(list(by_slug.values()))
    rows.sort(key=lambda r: (r[0].get("start_date") is None, r[0].get("start_date") or ""))
    print_summary(rows)
    print(f"\nWrote {TOURNAMENTS_PATH} ({len(by_slug)} tournaments)")


if __name__ == "__main__":
    main()
