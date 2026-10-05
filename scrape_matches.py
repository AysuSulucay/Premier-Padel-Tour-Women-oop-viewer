import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import date

from tournaments import Tournament

_WIDGET_BASE = "https://widget.matchscorerlive.com/screen/oopbyday"
WIDGET_ORIGIN = "https://widget.matchscorerlive.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def get_today_day(tournament: Tournament) -> int:
    delta = (date.today() - tournament.start_date).days + 1
    return max(1, min(delta, tournament.total_days))


def fetch_oop_url(tournament: Tournament, day: int) -> str:
    """Construct the OOP widget URL directly (day 1 = tournament start date)."""
    return f"{_WIDGET_BASE}/FIP-{tournament.year}-{tournament.tournament_id}/{day}?t=tol"


def fetch_widget_html(oop_url: str) -> str:
    resp = requests.get(oop_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


# ── Widget HTML extraction helpers ────────────────────────────────────────────

def extract_stylesheets(html: str) -> list[str]:
    """Return absolute stylesheet URLs from the widget page's <head>."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for link in soup.find_all("link"):
        rel = link.get("rel", [])
        rel_str = " ".join(rel) if isinstance(rel, list) else str(rel)
        if "stylesheet" not in rel_str.lower():
            continue
        href = link.get("href", "").strip()
        if not href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = WIDGET_ORIGIN + href
        urls.append(href)
    return urls


def _make_url_absolute(url: str) -> str:
    """Convert a widget-relative URL to an absolute URL."""
    if not url or url.startswith(("data:", "#", "http:", "https:", "mailto:")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return WIDGET_ORIGIN + url
    return url  # relative-path (rare) — leave as-is


def extract_widget_body(html: str) -> str:
    """
    Extract only the court/match content from the widget HTML.

    Widget structure (confirmed):
        <body class="m-3">
          <div class="sticky-top day-selector">  ← widget date nav  (REMOVE)
          <div class="row">                       ← court columns    (KEEP)
          <div class="modal fade p-0">            ← video modal      (REMOVE)
          <script>...</script>                    ← already stripped
          <style>...</style>                      ← inline styles    (KEEP)
        </body>

    Returns the court row + inline <style> tags wrapped in a div that
    carries the body's original classes (e.g. "m-3") so card spacing
    matches the widget exactly.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strip scripts
    for script in soup.find_all("script"):
        script.decompose()

    # Locate court columns → parent row → their container (body in this widget)
    court_cols = soup.find_all(
        "div",
        class_=lambda c: c and "col-lg-4" in c and "col-md-6" in c,
    )

    if court_cols:
        court_row = court_cols[0].parent   # <div class="row">
        container = court_row.parent       # <body class="m-3">

        # Remove everything except the court row (strips date nav, modal, scripts,
        # and inline <style> tags — all needed CSS is in the linked stylesheet files)
        for child in list(container.children):
            if not hasattr(child, "name") or not child.name:
                continue  # skip NavigableString / whitespace
            if child is not court_row:
                child.decompose()

        target = container

        # Also strip any <style> tags that may be nested inside the court row
        for style in target.find_all("style"):
            style.decompose()
    else:
        target = soup.find("body") or soup

    # Make relative URLs absolute
    for tag in target.find_all(True):
        for attr in ("src", "href", "action"):
            val = tag.get(attr)
            if isinstance(val, str):
                tag[attr] = _make_url_absolute(val)

    # Preserve body classes (e.g. "m-3") as a wrapper div so the card
    # margins / padding match what the original widget page produces.
    if target.name == "body":
        body_cls = " ".join(target.get("class", []))
        inner = target.decode_contents()
        wrapper_cls = f' class="{body_cls}"' if body_cls else ""
        return f"<div{wrapper_cls}>\n{inner}\n</div>"
    return str(target)


def filter_gender_html(body_html: str, gender: str = "Women") -> str:
    """
    Remove match tables for the wrong gender from widget body HTML.
    Also removes court columns that become entirely empty after filtering.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    for table in soup.find_all("table", class_="w-100"):
        round_div = table.find("div", class_="round-name")
        if not round_div:
            continue
        b_tag = round_div.find("b")
        cat = b_tag.get_text(strip=True) if b_tag else ""
        if cat and cat != gender:
            table.decompose()
    for col in soup.find_all(
        "div",
        class_=lambda c: c and "col-lg-4" in c and "col-md-6" in c,
    ):
        if not col.find("table", class_="w-100"):
            col.decompose()
    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    return body_el.decode_contents() if body_el else str(soup)


def _flag_src_to_country(src: str) -> str:
    """'/images/flags/ESP.jpg' → 'ESP'"""
    m = re.search(r'/flags/([A-Z]{2,3})\.', src, re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _parse_player_row(player_div) -> dict:
    """
    Parse one player from a div.d-flex.align-items-center block:
      <img class="flags" src="/images/flags/ESP.jpg"/>
      <div class="ml-2 line-thin">
        <span>A.</span>
        <span class="">Sanchez Fallada</span>
        <small>(3)</small>   ← optional seed
      </div>
    """
    flag_img = player_div.find("img", class_="flags")
    nationality = _flag_src_to_country(flag_img["src"]) if flag_img else ""

    name_div = player_div.find("div", class_="line-thin")
    if not name_div:
        return {}

    spans = name_div.find_all("span")
    first_initial = spans[0].get_text(strip=True) if len(spans) > 0 else ""
    last_name = spans[1].get_text(strip=True) if len(spans) > 1 else ""
    full_name = f"{first_initial} {last_name}".strip()

    seed_tag = name_div.find("small")
    seed = None
    if seed_tag:
        seed_text = seed_tag.get_text(strip=True).strip("()")
        if seed_text.isdigit():
            seed = seed_text

    return {
        "full_name": full_name,
        "nationality": nationality,
        "seed": seed,
        "score": [],
    }


def _parse_team_players(team_td) -> list[dict]:
    player_name_div = team_td.find("div", class_="player-names")
    if not player_name_div:
        return []
    double_div = player_name_div.find("div", class_="double")
    if not double_div:
        return []
    players = []
    for pdiv in double_div.find_all("div", class_="d-flex"):
        p = _parse_player_row(pdiv)
        if p:
            players.append(p)
    return players


def _parse_set_scores(row) -> list[int]:
    scores = []
    for td in row.find_all("td", class_="set"):
        # Use only the first text node — ignores <sup> tie-break digits
        # e.g. "6<sup>3</sup>" → "6", not "63"
        text = next(td.stripped_strings, "")
        if text.isdigit():
            scores.append(int(text))
        # "-" = set not played, skip
    return scores


def _parse_match_table(table, court_name: str, slot_idx: int) -> dict | None:
    rows = table.find_all("tr")
    if len(rows) < 3:
        return None

    # Row 0: header
    header_row = rows[0]
    # Status determined later after reading scores + ballg presence
    status = "upcoming"

    # Time note (slot note per match, e.g. "Starting at 9:00 AM" / "Followed by" / "Not before...")
    time_span = header_row.find("span", class_="court-name")
    time_note = time_span.get_text(strip=True) if time_span else ""

    # Round and category
    round_div = header_row.find("div", class_="round-name")
    category = ""
    round_name = ""
    if round_div:
        b_tag = round_div.find("b")
        div_tag = round_div.find("div")
        category = b_tag.get_text(strip=True) if b_tag else ""
        round_name = div_tag.get_text(strip=True) if div_tag else ""

    # Team A = scorebox-sep-bottom row
    team_a_row = None
    team_b_row = None
    for i, r in enumerate(rows[1:], start=1):
        cls = " ".join(r.get("class", []))
        if "scorebox-sep-bottom" in cls:
            team_a_row = r
            # Team B is the next row (skip summary rows)
            for r2 in rows[i + 1:]:
                cls2 = " ".join(r2.get("class", []))
                if "summary" not in cls2:
                    team_b_row = r2
                    break
            break

    if not team_a_row or not team_b_row:
        return None

    team_a_td = team_a_row.find("td", class_="team")
    team_b_td = team_b_row.find("td", class_="team")
    team_a = _parse_team_players(team_a_td) if team_a_td else []
    team_b = _parse_team_players(team_b_td) if team_b_td else []

    # Scores per set
    score_a = _parse_set_scores(team_a_row)
    score_b = _parse_set_scores(team_b_row)
    for i, p in enumerate(team_a):
        p["score"] = score_a
    for i, p in enumerate(team_b):
        p["score"] = score_b

    # Status: live > completed (has scores) > upcoming (no scores)
    if table.find("img", class_="ballg"):
        status = "in_progress"
    elif score_a or score_b:
        status = "completed"

    return {
        "court": court_name,
        "slot": slot_idx,
        "category": category,
        "round": round_name,
        "time_note": time_note,
        "status": status,
        "team_a": team_a,
        "team_b": team_b,
    }


def parse_widget(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    # Each court column is col-lg-4 col-md-6 col-sm-12 col-12
    court_cols = soup.find_all(
        "div",
        class_=lambda c: c and "col-lg-4" in c and "col-md-6" in c
    )

    for col in court_cols:
        court_el = col.find("div", class_="oop-court")
        court_name = court_el.get_text(strip=True) if court_el else "Unknown Court"

        tables = col.find_all("table", class_="w-100")
        for slot_idx, table in enumerate(tables, start=1):
            match = _parse_match_table(table, court_name, slot_idx)
            if match:
                matches.append(match)

    return matches


def fetch_one_day(tournament: Tournament, day: int, gender: str = "Women") -> tuple[list[dict], str]:
    """Fetch a single tournament day (for the watch-loop partial update).

    Returns (filtered_matches, body_html). Retries on transient errors.
    Raises RuntimeError if all attempts fail.
    """
    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            oop_url = fetch_oop_url(tournament, day)
            html = fetch_widget_html(oop_url)
            body = extract_widget_body(html)
            body_filtered = filter_gender_html(body, gender)
            all_matches = parse_widget(html)
            filtered = [m for m in all_matches if m.get("category") == gender]
            return filtered, body_filtered
        except Exception as exc:
            last_exc = exc
            if "404" in str(exc):
                break
            if attempt < _MAX_RETRIES:
                print(f"[scrape] Day {day} attempt {attempt} failed ({exc}) — retrying in {_RETRY_DELAY}s...")
                time.sleep(_RETRY_DELAY)
    raise RuntimeError(f"Day {day} fetch failed after {_MAX_RETRIES} attempts: {last_exc}")


def scrape_matches(tournament: Tournament, day: int | None = None) -> tuple[list[dict], str]:
    """Return (matches, oop_url)."""
    if day is None:
        day = get_today_day(tournament)
    print(f"[scrape_matches] Tournament day: {day}")

    oop_url = fetch_oop_url(tournament, day)
    print(f"[scrape_matches] OOP widget URL: {oop_url}")

    html = fetch_widget_html(oop_url)
    print(f"[scrape_matches] Widget HTML fetched ({len(html):,} bytes)")

    matches = parse_widget(html)
    print(f"[scrape_matches] Parsed {len(matches)} match cards")

    return matches, oop_url


_MAX_RETRIES = 3
_RETRY_DELAY = 3  # seconds between retries


def scrape_all_days(
    tournament: Tournament,
    gender: str = "Women",
) -> tuple[dict[int, list[dict]], dict[int, str], list[str], list[int], int]:
    """
    Fetch tournament days up to and including today.

    Returns a 5-tuple:
        matches_by_day : {day_num: [match_dict, ...]}  — parsed match data (for stats)
        bodies_by_day  : {day_num: body_html}           — gender-filtered widget HTML per day
        stylesheet_urls: [url, ...]                     — CSS links from widget <head>
        failed_days    : [day_num, ...]                 — days that errored (404 = no data, not a failure)
        total_matches  : matches of every category      — > 0 with no match of *gender*: that draw was not played

    Future days return empty entries. Days with errors return empty entries.
    Each day is retried up to _MAX_RETRIES times on transient errors.
    """
    today_day = get_today_day(tournament)
    print(f"[scrape_matches] Today = tournament day {today_day} ({tournament.dates[today_day - 1]})")

    days_matches: dict[int, list[dict]] = {}
    bodies_by_day: dict[int, str] = {}
    stylesheet_urls: list[str] = []
    failed_days: list[int] = []
    total_matches = 0

    for day in range(1, tournament.total_days + 1):
        if day > today_day:
            days_matches[day] = []
            bodies_by_day[day] = ""
            print(f"[scrape_matches]   Day {day}: skipped (future date)")
            continue

        print(f"[scrape_matches] Fetching day {day}/{today_day}...")
        last_exc = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                oop_url = fetch_oop_url(tournament, day)
                html = fetch_widget_html(oop_url)

                # Collect stylesheet URLs once from the first successful fetch
                if not stylesheet_urls:
                    stylesheet_urls = extract_stylesheets(html)
                    print(f"[scrape_matches]   Collected {len(stylesheet_urls)} widget stylesheet(s)")

                # Store gender-filtered body HTML
                body = extract_widget_body(html)
                bodies_by_day[day] = filter_gender_html(body, gender)

                # Also parse structured data for match counts / stats
                all_matches = parse_widget(html)
                filtered = [m for m in all_matches if m.get("category") == gender]
                days_matches[day] = filtered
                total_matches += len(all_matches)
                print(f"[scrape_matches]   Day {day}: {len(filtered)} {gender} match(es) (total parsed: {len(all_matches)})")
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                # Don't retry 404s — that day hasn't been published yet
                if "404" in str(exc):
                    break
                if attempt < _MAX_RETRIES:
                    print(f"[scrape_matches]   Day {day}: attempt {attempt} failed ({exc}) — retrying in {_RETRY_DELAY}s...")
                    time.sleep(_RETRY_DELAY)
        if last_exc is not None:
            print(f"[scrape_matches]   Day {day}: ERROR - {last_exc}")
            days_matches[day] = []
            bodies_by_day[day] = ""
            if "404" not in str(last_exc):
                failed_days.append(day)
        if day < today_day:
            time.sleep(0.3)

    return days_matches, bodies_by_day, stylesheet_urls, failed_days, total_matches
