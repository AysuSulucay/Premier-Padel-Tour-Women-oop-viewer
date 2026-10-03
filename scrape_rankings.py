import json
import re
import tempfile
import time
import os
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup
from unidecode import unidecode

RANKINGS_URL = "https://www.padelfip.com/fip-rankings/"
RANKING_API = "https://www.padelfip.com/wp-json/fip/v1/ranking/load-more"
CACHE_PATH = Path(__file__).parent / "data" / "rankings_cache.json"
CACHE_TTL_HOURS = 24

_NAME_NAT_RE = re.compile(r"^(.+?)\s+([A-Z]{3})\s+(.+?)\s+([A-Z]{3})\s*$")
_RANK_LINE_RE = re.compile(r"^(\d+)\s+(?:WC\s+)?(\d+)\s+(\d+)\s+\d[\d,. ]*$")
_POINTS_LINE_RE = re.compile(r"^([\d,.]+)\s+points\s+([\d,.]+)\s+points\s*$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": RANKINGS_URL,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def _get_week_and_year() -> tuple[int, int]:
    """Read week-no and year from the Load More button on the rankings page."""
    resp = requests.get(RANKINGS_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    btn = soup.find("button", class_="loadMoreRanking", attrs={"data-gender": "male"})
    if btn:
        week = int(btn.get("data-week-no", 0))
        year = int(btn.get("data-year", datetime.now().year))
        return week, year
    # fallback: extract from any load-more button
    btn = soup.find("button", class_="loadMoreRanking")
    if btn:
        week = int(btn.get("data-week-no", 0))
        year = int(btn.get("data-year", datetime.now().year))
        return week, year
    raise ValueError("Could not find week-no on rankings page")


MAX_PLAYERS_PER_GENDER = 400


def _fetch_all_players(gender: str, week: int, year: int) -> list[dict]:
    """Paginate through ranked players of a given gender (stops on duplicate IDs or max limit)."""
    players = []
    seen_ids: set[str] = set()
    offset = 0
    limit = 20
    while len(players) < MAX_PLAYERS_PER_GENDER:
        resp = requests.get(
            RANKING_API,
            params={
                "category": "master",
                "circuit": "premierpadel",
                "gender": gender,
                "offset": offset,
                "limit": limit,
                "week": week,
                "year": year,
                "lang": "en",
            },
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break

        new_in_batch = 0
        for p in batch:
            pid = p.get("player_id", "")
            if pid and pid in seen_ids:
                # API is cycling — stop
                print(f"  [{gender}] Duplicate player_id detected at offset={offset}, stopping")
                return players
            if pid:
                seen_ids.add(pid)
            players.append(p)
            new_in_batch += 1

        print(f"  [{gender}] offset={offset} → {new_in_batch} players (total: {len(players)})")
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return players


def _slug_from_url(url: str) -> str:
    """'https://www.padelfip.com/player/arturo-coello/' → 'arturo-coello'"""
    m = re.search(r'/player/([^/]+)/?$', url)
    return m.group(1) if m else ""


def _build_cache(players: list[dict]) -> dict:
    cache = {}
    for p in players:
        slug = _slug_from_url(p.get("url", ""))
        full_name = f"{p.get('name', '')} {p.get('surname', '')}".strip()
        if not slug or not full_name:
            continue
        cache[slug] = {
            "rank": p.get("rank"),
            "full_name": full_name,
            "profile_url": p.get("url", ""),
            "nationality": p.get("country_name", ""),
            "points": p.get("points"),
        }
    return cache


def scrape_rankings() -> dict:
    """Fetch all FIP rankings and return {slug: player_info}."""
    print("[rankings] Detecting current week/year from FIP page...")
    week, year = _get_week_and_year()
    print(f"[rankings] Week={week}, Year={year}")

    all_players = []
    for gender in ("male", "female"):
        print(f"[rankings] Fetching {gender} players...")
        players = _fetch_all_players(gender, week, year)
        all_players.extend(players)
        print(f"[rankings] {gender}: {len(players)} players fetched")

    cache = _build_cache(all_players)
    print(f"[rankings] Total unique players in cache: {len(cache)}")
    return cache


def _cache_is_fresh() -> bool:
    if not CACHE_PATH.exists():
        return False
    mtime = os.path.getmtime(CACHE_PATH)
    age_hours = (time.time() - mtime) / 3600
    return age_hours < CACHE_TTL_HOURS


def get_rankings(force_refresh: bool = False) -> dict:
    """Load from cache if fresh, else re-scrape."""
    if not force_refresh and _cache_is_fresh():
        print(f"[rankings] Using cached rankings ({CACHE_PATH})")
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    print("[rankings] Cache stale or missing — scraping FIP rankings...")
    cache = scrape_rankings()

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"[rankings] Cache saved → {CACHE_PATH}")
    return cache


# ── Name Matching ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return unidecode(text).lower().strip()


def _make_lookup_index(cache: dict) -> dict:
    """
    Build a lookup dict: (first_initial, last_name_fragment) → [slugs]

    For "Martina Calvo Santamaria" (parts = [martina, calvo, santamaria])
    first_initial = 'm', last_parts = [calvo, santamaria]
    Generates all contiguous suffixes of last_parts:
      ("m", "calvo santamaria"), ("m", "calvo"), ("m", "santamaria")
    So "M. Calvo" and "M. Santamaria" both resolve correctly.
    """
    index: dict[tuple, list] = {}
    for slug, info in cache.items():
        full = _normalize(info["full_name"])
        parts = full.split()
        if len(parts) < 2:
            continue
        first_initial = parts[0][0]
        last_parts = parts[1:]
        # Generate all contiguous sub-sequences of last_parts
        n = len(last_parts)
        for start in range(n):
            for end in range(start + 1, n + 1):
                fragment = " ".join(last_parts[start:end])
                key = (first_initial, fragment)
                index.setdefault(key, []).append(slug)
    return index


def match_player(widget_name: str, cache: dict, index: dict) -> dict | None:
    """
    Match a widget abbreviated name (e.g. 'A. Sanchez Fallada') to a cache entry.
    Returns the cache entry dict or None.
    """
    norm = _normalize(widget_name)
    parts = norm.split()
    if not parts:
        return None

    # Extract first initial (strip trailing dot)
    raw_first = parts[0].rstrip(".")
    first_initial = raw_first[0] if raw_first else ""

    # The rest is the last name (may be multi-word)
    last_parts = parts[1:]
    if not last_parts:
        return None

    # Try longest last name first (handles "sanchez fallada")
    for n in range(len(last_parts), 0, -1):
        last_name = " ".join(last_parts[:n])
        key = (first_initial, last_name)
        candidates = index.get(key, [])
        if len(candidates) == 1:
            return cache[candidates[0]]
        if len(candidates) > 1:
            # Multiple matches — pick highest ranked
            return min(
                (cache[s] for s in candidates),
                key=lambda x: x.get("rank") or 9999
            )
    return None


def enrich_players(matches: list[dict], cache: dict) -> list[dict]:
    """Add rank/profile_url to each player in every match."""
    index = _make_lookup_index(cache)
    enriched = []
    for match in matches:
        match = dict(match)
        for team_key in ("team_a", "team_b"):
            enriched_team = []
            for player in match[team_key]:
                p = dict(player)
                result = match_player(p["full_name"], cache, index)
                if result:
                    p["rank"] = result["rank"]
                    p["profile_url"] = result["profile_url"]
                else:
                    p["rank"] = None
                    p["profile_url"] = None
                enriched_team.append(p)
            match[team_key] = enriched_team
        enriched.append(match)
    return enriched


# ── PDF Entry List Rankings ───────────────────────────────────────────────────

def _entry_cache_is_fresh(cache_path: Path, frozen: bool = False) -> bool:
    if not cache_path.exists():
        return False
    if frozen:
        return True
    age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
    return age_hours < CACHE_TTL_HOURS


def _download_pdf(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content


def _parse_table_data(table_data: list) -> list[dict]:
    players = []
    for row in table_data:
        if not row or not row[0]:
            continue
        cells = [c.strip() if isinstance(c, str) else "" for c in row]
        if not cells[0].isdigit():
            continue
        rank = int(cells[0])
        full_name = cells[1] if len(cells) > 1 else ""
        nationality = cells[2] if len(cells) > 2 else ""
        points_str = cells[3] if len(cells) > 3 else ""
        try:
            points = int(points_str.replace(",", "").replace(".", ""))
        except (ValueError, AttributeError):
            points = None
        if full_name and nationality and len(nationality) == 3 and nationality.isupper():
            players.append({"rank": rank, "full_name": full_name, "nationality": nationality, "points": points})
    return players


def _parse_text_line(line: str) -> dict | None:
    m = _PDF_ROW_RE.match(line)
    if not m:
        return None
    try:
        points = int(m.group(4).replace(",", "").replace(".", ""))
    except ValueError:
        points = None
    return {
        "rank": int(m.group(1)),
        "full_name": m.group(2).strip(),
        "nationality": m.group(3),
        "points": points,
    }


def _parse_pdf_players(pdf_bytes: bytes) -> list[dict]:
    """
    Parse the FIP entry list PDF.
    Each team pair occupies three consecutive lines:
      Line 1: "{Name1} {NAT1} {Name2} {NAT2}"
      Line 2: "{pos} [WC] {rank1} {rank2} {total_pts}"
      Line 3: "{pts1} points {pts2} points"
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    lines = []
    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    line = line.strip()
                    if line:
                        lines.append(line)
    finally:
        os.unlink(tmp_path)

    players = []
    i = 0
    while i < len(lines) - 1:
        name_m = _NAME_NAT_RE.match(lines[i])
        rank_m = _RANK_LINE_RE.match(lines[i + 1]) if name_m else None
        if name_m and rank_m:
            name1, nat1 = name_m.group(1).strip(), name_m.group(2)
            name2, nat2 = name_m.group(3).strip(), name_m.group(4)
            rank1, rank2 = int(rank_m.group(2)), int(rank_m.group(3))

            pts1, pts2 = None, None
            advance = 2
            if i + 2 < len(lines):
                pts_m = _POINTS_LINE_RE.match(lines[i + 2])
                if pts_m:
                    advance = 3
                    try:
                        pts1 = int(pts_m.group(1).replace(",", "").replace(".", ""))
                        pts2 = int(pts_m.group(2).replace(",", "").replace(".", ""))
                    except ValueError:
                        pass

            players.append({"rank": rank1, "full_name": name1, "nationality": nat1, "points": pts1})
            players.append({"rank": rank2, "full_name": name2, "nationality": nat2, "points": pts2})
            i += advance
        else:
            i += 1

    seen: set[str] = set()
    unique = []
    for p in players:
        key = unidecode(p["full_name"]).lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return sorted(unique, key=lambda x: x["rank"])


def _build_entry_cache(players: list[dict]) -> dict:
    cache = {}
    for p in players:
        full_name = p.get("full_name", "").strip()
        if not full_name:
            continue
        slug = re.sub(r"[^a-z0-9-]", "", unidecode(full_name).lower().replace(" ", "-"))
        cache[slug] = {
            "rank": p.get("rank"),
            "full_name": full_name,
            "profile_url": f"https://www.padelfip.com/player/{slug}/",
            "nationality": p.get("nationality", ""),
            "points": p.get("points"),
        }
    return cache


def get_rankings_from_pdf(
    pdf_url: str | None,
    cache_path: Path,
    local_pdf_path: Path | None = None,
    force_refresh: bool = False,
    frozen: bool = False,
) -> dict:
    """
    Download the tournament entry list PDF and return {slug: player_info}.

    The parsed result is cached per tournament at ``cache_path``. If the
    download fails (or there is no URL), ``local_pdf_path`` — the copy saved
    by discover_tournaments.py — is used instead. With neither, returns {}.
    ``frozen`` (finished tournament) makes an existing cache valid forever.
    """
    if not force_refresh and _entry_cache_is_fresh(cache_path, frozen):
        print(f"[rankings] Using cached entry list ({cache_path})")
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    pdf_bytes = None
    if pdf_url:
        print("[rankings] Downloading entry list PDF...")
        try:
            pdf_bytes = _download_pdf(pdf_url)
            print(f"[rankings] PDF downloaded ({len(pdf_bytes):,} bytes)")
        except requests.RequestException as exc:
            print(f"[rankings] WARNING: PDF download failed ({exc})")
    if pdf_bytes is None and local_pdf_path and local_pdf_path.exists():
        print(f"[rankings] Using local PDF copy ({local_pdf_path})")
        pdf_bytes = local_pdf_path.read_bytes()
    if pdf_bytes is None:
        print("[rankings] WARNING: No entry list PDF available — rank badges skipped.")
        return {}

    print("[rankings] Parsing players from PDF...")
    players = _parse_pdf_players(pdf_bytes)
    print(f"[rankings] {len(players)} players parsed from PDF")

    if not players:
        print("[rankings] WARNING: No players parsed. Run _debug_pdf_rows() to inspect PDF format.")

    cache = _build_entry_cache(players)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"[rankings] Entry list cache saved -> {cache_path}")
    return cache


def build_lookup_index(cache: dict) -> dict:
    """Public wrapper for _make_lookup_index.

    Build a name-fragment lookup index from a rankings cache dict, for use
    with match_player().
    """
    return _make_lookup_index(cache)


def _debug_pdf_rows(pdf_url: str) -> None:
    """Print raw pdfplumber output to help tune the parser."""
    print(f"[debug] Downloading {pdf_url}...")
    pdf_bytes = _download_pdf(pdf_url)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        with pdfplumber.open(tmp_path) as pdf:
            for i, page in enumerate(pdf.pages):
                print(f"\n--- Page {i + 1} TEXT ---")
                for line in (page.extract_text() or "").splitlines():
                    print(repr(line))
                print(f"\n--- Page {i + 1} TABLES ---")
                for j, table in enumerate(page.extract_tables()):
                    print(f"  Table {j + 1}:")
                    for row in table:
                        print(f"    {row}")
    finally:
        os.unlink(tmp_path)
