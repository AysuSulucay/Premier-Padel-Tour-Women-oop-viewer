import json
import re
import tempfile
import time
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup
from unidecode import unidecode

RANKINGS_URL = "https://www.padelfip.com/fip-rankings/"
RANKING_API = "https://www.padelfip.com/wp-json/fip/v1/ranking/load-more"
PROFILE_URL = "https://www.padelfip.com/player/{}/"
DATA_DIR = Path(__file__).parent / "data"
PROFILES_PATH = DATA_DIR / "fip_profiles.json"
ALIASES_PATH = DATA_DIR / "player_aliases.json"
CACHE_TTL_HOURS = 24
RANKING_PAGE_SIZE = 1000   # the API returns nothing for a larger page


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


# ── Player profiles on padelfip.com ───────────────────────────────────────────
# A rank badge links to the player's profile. Its address is the slug of her name as FIP
# spells it ('gemma-triay-pons'); an entry list can spell it shorter, and padelfip.com then
# redirects '/player/gemma-triay/' to some other page. So the address is taken from the FIP
# ranking list; a player who is no longer ranked is looked up once.

def _get_week_and_year() -> tuple[int, int]:
    """Read week-no and year from the Load More button on the rankings page."""
    resp = requests.get(RANKINGS_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    btn = BeautifulSoup(resp.text, "html.parser").find("button", class_="loadMoreRanking")
    if not btn:
        raise ValueError("Could not find week-no on rankings page")
    return int(btn.get("data-week-no", 0)), int(btn.get("data-year", datetime.now().year))


def _slug_from_url(url: str) -> str:
    """'https://www.padelfip.com/player/arturo-coello/' → 'arturo-coello'"""
    m = re.search(r'/player/([^/]+)/?$', url)
    return m.group(1) if m else ""


def _fetch_ranked_women() -> dict[str, str]:
    """Every woman of the FIP ranking: {profile slug: full name}."""
    week, year = _get_week_and_year()
    ranked: dict[str, str] = {}
    offset = 0
    while True:
        resp = requests.get(
            RANKING_API,
            params={
                "category": "master", "circuit": "premierpadel", "gender": "female",
                "offset": offset, "limit": RANKING_PAGE_SIZE, "week": week, "year": year, "lang": "en",
            },
            headers=HEADERS,
            timeout=60,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list):
            break
        page = {
            _slug_from_url(p.get("url", "")): f"{p.get('name', '')} {p.get('surname', '')}".strip()
            for p in batch
        }
        page.pop("", None)
        if not page.keys() - ranked.keys():   # empty page, or the API started over
            break
        ranked.update(page)
        if len(batch) < RANKING_PAGE_SIZE:
            break
        offset += RANKING_PAGE_SIZE
        time.sleep(0.3)
    return ranked


_profiles: dict | None = None


def _save_profiles() -> None:
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(_profiles, f, ensure_ascii=False, indent=1)


def _load_profiles(force_refresh: bool = False) -> dict:
    """data/fip_profiles.json: ``{fetched, ranked: {profile slug: full name}, checked: {slug: bool}}``.

    ``ranked`` is fetched again when older than 24 h (once per run); ``checked`` — does
    padelfip.com have a profile at this slug, for players who are not ranked — is kept for good.
    """
    global _profiles
    if _profiles is None:
        _profiles = {"fetched": 0, "ranked": {}, "checked": {}}
        if PROFILES_PATH.exists():
            with open(PROFILES_PATH, encoding="utf-8") as f:
                _profiles.update(json.load(f))
        if force_refresh or (time.time() - _profiles["fetched"]) / 3600 >= CACHE_TTL_HOURS:
            print("[profiles] Fetching the women's FIP ranking list...")
            try:
                _profiles["ranked"] = _fetch_ranked_women()
                _profiles["fetched"] = time.time()
                _save_profiles()
                print(f"[profiles] {len(_profiles['ranked'])} ranked players")
            except (requests.RequestException, ValueError) as exc:
                print(f"[profiles] WARNING: ranking list not fetched ({exc}) — using the previous one")
    return _profiles


def _profile_exists(slug: str) -> bool | None:
    """Does padelfip.com have a profile page at this slug? None when the request gave no answer.

    A redirect is no: the site sends an unknown slug to whatever page has a similar address.
    """
    try:
        resp = requests.get(
            PROFILE_URL.format(slug), headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=20, allow_redirects=False, stream=True,
        )
        resp.close()
    except requests.RequestException as exc:
        print(f"[profiles] WARNING: {slug}: {exc}")
        return None
    if resp.status_code == 200:
        return True
    return False if resp.status_code in (301, 302, 404) else None


def load_aliases() -> dict[str, str]:
    """Hand-kept spellings no rule connects (typos, abbreviations): {variant slug: the player's usual slug}."""
    if not ALIASES_PATH.exists():
        return {}
    with open(ALIASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _slug_words(slug: str) -> tuple:
    return tuple(w for w in slug.split("-") if w)


def _ranked_profile(words: tuple, ranked: dict[str, tuple]) -> str | None:
    """Profile slug of the one ranked player these name words belong to.

    *ranked* is ``{profile slug: name words}``. The same name first; else the player whose
    name is this one with words left out, or the other way round ('Martina Calvo' /
    'Martina Calvo Santamaria', 'Maria Virginia Riera' / 'Virginia Riera') — surnames
    dropped at the end are tried first. Several players that fit → None.
    """
    same = [slug for slug, name in ranked.items() if name == words]
    fits = [slug for slug, name in ranked.items() if is_short_form(words, name) or is_short_form(name, words)]
    for candidates in (same, [s for s in fits if ranked[s][:len(words)] == words], fits):
        if candidates:
            return candidates[0] if len(candidates) == 1 else None
    return None


def add_profile_urls(cache: dict, long_names=(), known_names=(), force_refresh: bool = False) -> None:
    """Set ``profile_url`` of every player of an entry list cache ('' when no profile is found).

    A player is looked up under her fuller name from other entry lists (``long_names``, see
    build_lookup_index), else under this list's spelling: the ranked player the name belongs
    to, else the name's own slug when padelfip.com has a profile there (asked once, kept in
    data/fip_profiles.json). A name that fits several of ``known_names`` — every player of
    every entry list — gets no link rather than a wrong one ('Cristina Gonzalez').
    """
    profiles = _load_profiles(force_refresh)
    ranked = {slug: _slug_words(player_slug(name)) for slug, name in profiles["ranked"].items()}
    known = {_slug_words(player_slug(name)) for name in known_names}
    aliases = load_aliases()
    fuller = _long_forms(cache, long_names)
    asked = 0
    for slug, info in cache.items():
        longer = fuller.get(slug, [])
        words = _slug_words(player_slug(longer[0]) if len(longer) == 1 else aliases.get(slug, slug))
        ambiguous = sum(is_short_form(words, other) for other in known) > 1
        found = None if ambiguous else _ranked_profile(words, ranked)
        if not found and not ambiguous:
            guess = "-".join(words)
            if guess not in profiles["checked"]:
                if asked:
                    time.sleep(0.5)
                asked += 1
                exists = _profile_exists(guess)
                if exists is not None:
                    profiles["checked"][guess] = exists
            found = guess if profiles["checked"].get(guess) else None
        info["profile_url"] = PROFILE_URL.format(found) if found else ""
    if asked:
        _save_profiles()
        print(f"[profiles] {asked} profile page(s) looked up on padelfip.com")


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
        for key in _name_keys(info["full_name"]):
            index.setdefault(key, []).append(slug)
    return index


def _name_keys(full_name: str) -> list[tuple]:
    """(first_initial, last_name_fragment) keys of one full name — see _make_lookup_index."""
    parts = _normalize(full_name).split()
    if len(parts) < 2:
        return []
    first_initial = parts[0][0]
    last_parts = parts[1:]
    # All contiguous sub-sequences of last_parts
    n = len(last_parts)
    return [
        (first_initial, " ".join(last_parts[start:end]))
        for start in range(n) for end in range(start + 1, n + 1)
    ]


def is_short_form(short: tuple, long: tuple) -> bool:
    """True when the name *short* is *long* with words left out, the first or the last word kept
    ('Virginia Riera' / 'Maria Virginia Riera'; both as tuples of slug words)."""
    if not 2 <= len(short) < len(long) or (short[0] != long[0] and short[-1] != long[-1]):
        return False
    rest = iter(long)
    return all(word in rest for word in short)


def match_candidates(widget_name: str, index: dict) -> list[str]:
    """
    Slugs that a widget abbreviated name (e.g. 'A. Sanchez Fallada') can refer to.
    More than one slug means the name is ambiguous ('A. Martinez').
    """
    norm = _normalize(widget_name)
    parts = norm.split()
    if not parts:
        return []

    # Extract first initial (strip trailing dot)
    raw_first = parts[0].rstrip(".")
    first_initial = raw_first[0] if raw_first else ""

    # The rest is the last name (may be multi-word)
    last_parts = parts[1:]
    if not last_parts:
        return []

    # Try longest last name first (handles "sanchez fallada")
    for n in range(len(last_parts), 0, -1):
        last_name = " ".join(last_parts[:n])
        candidates = index.get((first_initial, last_name), [])
        if candidates:
            return candidates
    return []


def match_player(widget_name: str, cache: dict, index: dict) -> dict | None:
    """
    Match a widget abbreviated name (e.g. 'A. Sanchez Fallada') to a cache entry.
    Returns the cache entry dict or None.
    """
    candidates = match_candidates(widget_name, index)
    if not candidates:
        return None
    if len(candidates) == 1:
        return cache[candidates[0]]
    # Multiple matches — pick highest ranked
    return min(
        (cache[s] for s in candidates),
        key=lambda x: x.get("rank") or 9999
    )


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


_PDF_SECTIONS = ("MAIN DRAW", "QUALIFICATIONS", "WAITING LIST")


_NUMBER_RE = re.compile(r"\d[\d.,]*")
_NAT_RE = re.compile(r"[A-Z]{2,3}")                  # 'ESP'; the 2023 lists write 'NL'
_GLUED_POINTS_RE = re.compile(r"(\d[\d.,]*)points", re.I)
# Not part of a name: protected ranking, wild card, no ranking, lucky loser ('Lli', '[PR]')
_MARKER_RE = re.compile(r"PR|WC|N/A|L[Ll][Ii]?|\[.*\]")
# … the lucky loser mark is also written in two words: 'LL i', '(LL i)'
_LUCKY_LOSER_RES = (re.compile(r"\(?L[Ll]"), re.compile(r"[iI]\)?"))
_HEADER_ROW_RE = re.compile(
    r"^(pos\.?\s|team$|points$|team points$)|last update|entry list|ranking player|race player", re.I
)
# 2023 lists without rankings: '1 ARIANA SANCHEZ // PAULA JOSEMARIA ESP/ESP 14680 14680 29360'
_SLASH_ROW_RE = re.compile(
    r"^\d+\s+(?:[Ww][Cc]\s+)?(.+?)\s*//\s*(.+?)\s+([A-Z]{2,3})\s*/\s*([A-Z]{2,3})\s+(\d[\d.,]*)\s+(\d[\d.,]*)\s+\d[\d.,]*$"
)
# Finals 2024, one pair per line: '1 1 Paula Josemaria ESP 1 Ariana Sanchez ESP'
_ONE_LINE_ROW_RE = re.compile(r"^\d+\s+(\d+)\s+(\D+?)\s+([A-Z]{3})\s+(\d+)\s+(\D+?)\s+([A-Z]{3})$")
_COLUMN_TOLERANCE = 2     # PDF points


def _to_int(text: str) -> int | None:
    try:
        return int(re.sub(r"[.,]", "", text))
    except ValueError:
        return None


def _row_words(row: list[dict]) -> list[dict]:
    """One row's words, left to right, without the two-word lucky loser mark."""
    row = sorted(row, key=lambda w: w["x0"])
    first, second = _LUCKY_LOSER_RES
    marks = {
        j for i in range(len(row) - 1)
        if first.fullmatch(row[i]["text"]) and second.fullmatch(row[i + 1]["text"])
        for j in (i, i + 1)
    }
    return [w for i, w in enumerate(row) if i not in marks]


def _page_rows(page) -> list[list[dict]]:
    """Text rows of one PDF page, top to bottom; each row is its pdfplumber words, left to right."""
    rows, row, top = [], [], None
    for word in sorted(page.extract_words(y_tolerance=3), key=lambda w: (w["top"], w["x0"])):
        if row and abs(word["top"] - top) > 3:
            rows.append(_row_words(row))
            row = []
        if not row:
            top = word["top"]
        row.append(word)
    if row:
        rows.append(_row_words(row))
    return rows


def _points_entries(row: list[dict]) -> list[tuple[float, int | None]]:
    """``[(x, points)]`` for every '<n> points' of a row (also 'N/A points' and '78points')."""
    entries = []
    for i, word in enumerate(row):
        if m := _GLUED_POINTS_RE.fullmatch(word["text"]):
            entries.append((word["x0"], _to_int(m.group(1))))
        elif word["text"].lower() == "points" and i:
            before = row[i - 1]
            if _NUMBER_RE.fullmatch(before["text"]) or before["text"] == "N/A":
                entries.append((before["x0"], _to_int(before["text"])))
    return entries


def _player_columns(xs: list[float]) -> tuple[float, float] | None:
    """Left edge of the two player columns, from the x of the points entries (None if only one side is seen)."""
    if not xs or max(xs) - min(xs) < 60:
        return None
    middle = (min(xs) + max(xs)) / 2
    left = Counter(round(x) for x in xs if x < middle).most_common(1)[0][0]
    right = Counter(round(x) for x in xs if x >= middle).most_common(1)[0][0]
    return left - _COLUMN_TOLERANCE, right - _COLUMN_TOLERANCE


def _block_players(block: list[dict], columns: tuple[float, float], points: list) -> list[dict] | None:
    """The two players of one pair, from the words above its points row (None if unreadable).

    Words are told apart by their x: left of the first player column stand the position and
    her ranking, between the two columns the partner's ranking; a name is every other word
    from its column's left edge on — also when it wraps onto a second line.
    """
    col1, col2 = columns
    names = ([], [])
    before, between = [], []
    for word in block:
        x, text = word["x0"], word["text"]
        if _NUMBER_RE.fullmatch(text):
            if x < col1:
                before.append((x, text))        # position, ranking 1
            elif x < col2:
                between.append((x, text))       # ranking 2
        elif x >= col1 and not _MARKER_RE.fullmatch(text):
            names[x >= col2].append(text)
    before.sort()
    ranks = (   # 0 stands for "no ranking"
        (_to_int(before[1][1]) or None) if len(before) >= 2 else None,
        (_to_int(between[0][1]) or None) if between else None,
    )
    players = []
    for words, rank, pts in zip(names, ranks, points):
        nat = words.pop() if len(words) > 1 and _NAT_RE.fullmatch(words[-1]) else ""
        # Letter-spaced text ('C a m i l a'), or two pairs run together
        if (not words or len(words) > 7 or sum(len(w) == 1 for w in words) >= 4
                or any(_NAT_RE.fullmatch(w) and len(w) == 3 for w in words)):
            return None
        players.append({"rank": rank, "full_name": " ".join(words), "nationality": nat, "points": pts})
    return players


def _parse_pdf_pairs(pdf_bytes: bytes) -> list[dict]:
    """
    Parse the FIP entry list PDF into pairs, in document order.

    A pair is a block of text rows that ends with its points row:
      "{Name1} {NAT1} {Name2} {NAT2}"          (a long name wraps; older lists omit the nationality)
      "{pos} [WC] {rank1} {rank2} {total_pts}" (older lists: above the names, or without rankings)
      "{pts1} points {pts2} points"
    The layout changed several times between 2023 and 2026, so words are assigned by their
    column (x position), not by their place in the line — see ``_block_players``. Two
    older layouts with one pair per line are read with ``_SLASH_ROW_RE`` / ``_ONE_LINE_ROW_RE``.

    Returns ``[{"section": "MAIN DRAW" | "QUALIFICATIONS" | "WAITING LIST", "players": [player, player]}]``
    with player = {rank, full_name, nationality, points}; ``rank`` and ``points`` are None and
    ``nationality`` is "" when the list does not give them.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        with pdfplumber.open(tmp_path) as pdf:
            pages = [_page_rows(page) for page in pdf.pages]
    finally:
        os.unlink(tmp_path)

    def player(rank, name, nat, pts):
        return {"rank": rank, "full_name": name, "nationality": nat, "points": pts}

    document_columns = _player_columns([x for rows in pages for row in rows for x, _ in _points_entries(row)])
    pairs = []
    section = _PDF_SECTIONS[0]   # the first pairs are the main draw, with or without a heading
    block: list[dict] = []
    for rows in pages:
        columns = _player_columns([x for row in rows for x, _ in _points_entries(row)]) or document_columns
        for row in rows:
            text = " ".join(w["text"] for w in row)
            entries = _points_entries(row)
            if text.upper() in _PDF_SECTIONS:
                section, block = text.upper(), []
            elif m := _SLASH_ROW_RE.match(text):
                name1, name2, nat1, nat2, pts1, pts2 = m.groups()
                pairs.append({"section": section, "players": [
                    player(None, name1.title(), nat1, _to_int(pts1)),
                    player(None, name2.title(), nat2, _to_int(pts2)),
                ]})
                block = []
            elif m := _ONE_LINE_ROW_RE.match(text):
                rank1, name1, nat1, rank2, name2, nat2 = m.groups()
                pairs.append({"section": section, "players": [
                    player(int(rank1), name1, nat1, None),
                    player(int(rank2), name2, nat2, None),
                ]})
                block = []
            elif entries and columns:
                points = [None, None]
                for x, value in entries:
                    points[x >= columns[1]] = value
                # A wrapped nationality can share the points row
                block += [w for w in row if _NAT_RE.fullmatch(w["text"]) and len(w["text"]) == 3]
                players = _block_players(block, columns, points)
                if players:
                    pairs.append({"section": section, "players": players})
                block = []
            elif _HEADER_ROW_RE.search(text):
                block = []
            else:
                block += row
    return pairs


def _parse_pdf_players(pdf_bytes: bytes) -> list[dict]:
    """Every player of the entry list PDF, de-duplicated and sorted by rank."""
    players = [p for pair in _parse_pdf_pairs(pdf_bytes) for p in pair["players"]]

    seen: set[str] = set()
    unique = []
    for p in players:
        key = unidecode(p["full_name"]).lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return sorted(unique, key=lambda x: x["rank"] or 9999)   # no ranking in the list → last


def player_slug(full_name: str) -> str:
    """'Ariana Sanchez Fallada' → 'ariana-sanchez-fallada'"""
    return re.sub(r"[^a-z0-9-]", "", unidecode(full_name).lower().replace(" ", "-"))


def _build_entry_cache(players: list[dict]) -> dict:
    cache = {}
    for p in players:
        full_name = p.get("full_name", "").strip()
        if not full_name:
            continue
        slug = player_slug(full_name)
        cache[slug] = {
            "rank": p.get("rank"),
            "full_name": full_name,
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
    ``frozen`` (finished tournament) makes an existing cache valid forever, and
    the local copy is read without trying the download.
    """
    if not force_refresh and _entry_cache_is_fresh(cache_path, frozen):
        print(f"[rankings] Using cached entry list ({cache_path})")
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    pdf_bytes = None
    has_local = bool(local_pdf_path and local_pdf_path.exists())
    if pdf_url and not (frozen and has_local):   # a finished tournament's list no longer changes
        print("[rankings] Downloading entry list PDF...")
        try:
            pdf_bytes = _download_pdf(pdf_url)
            print(f"[rankings] PDF downloaded ({len(pdf_bytes):,} bytes)")
        except requests.RequestException as exc:
            print(f"[rankings] WARNING: PDF download failed ({exc})")
    if pdf_bytes is None and has_local:
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


def build_lookup_index(cache: dict, long_names=()) -> dict:
    """Build a name-fragment lookup index from a rankings cache dict, for use with match_player().

    ``long_names``: fuller spellings of some of the players, known from other entry lists.
    The widget can show more surnames than this list ('A. Martinez Sanjuan' / 'Aida Martinez');
    with her longer name indexed too, that widget name finds her and not another A. Martinez.
    """
    index = _make_lookup_index(cache)
    for slug, names in _long_forms(cache, long_names).items():
        for name in names:
            for key in _name_keys(name):
                if slug not in index.setdefault(key, []):
                    index[key].append(slug)
    return index


def _long_forms(cache: dict, long_names) -> dict[str, list[str]]:
    """{cache slug: fuller spellings of her name} — each of *long_names* goes to the one player it fits."""
    words = {slug: _slug_words(slug) for slug in cache}
    listed = set(words.values())
    forms: dict[str, list[str]] = {}
    for name in long_names:
        long = _slug_words(player_slug(name))
        if long in listed:   # a player of this list under her full name, not someone's fuller spelling
            continue
        holders = [slug for slug in cache if is_short_form(words[slug], long)]
        if len(holders) == 1:
            forms.setdefault(holders[0], []).append(name)
    return forms


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


if __name__ == "__main__":   # python scrape_rankings.py — self-check of the profile matching
    _ranked = {
        "martina-calvo-santamaria": ("martina", "calvo", "santamaria"),
        "virginia-riera": ("virginia", "riera"),
        "maria-lopez": ("maria", "lopez"),
        "maria-cristina-lopez-fuertes": ("maria", "cristina", "lopez", "fuertes"),
        "kae-tokumoto-2": ("kae", "tokumoto"),
        "lucia-garcia-trella": ("lucia", "garcia", "trella"),
        "lucia-garcia-ruiz": ("lucia", "garcia", "ruiz"),
    }
    assert _ranked_profile(("martina", "calvo"), _ranked) == "martina-calvo-santamaria"
    assert _ranked_profile(("maria", "virginia", "riera"), _ranked) == "virginia-riera"
    assert _ranked_profile(("maria", "cristina", "lopez"), _ranked) == "maria-cristina-lopez-fuertes"
    assert _ranked_profile(("kae", "tokumoto"), _ranked) == "kae-tokumoto-2"
    assert _ranked_profile(("lucia", "garcia"), _ranked) is None   # fits two players
    assert _ranked_profile(("ana", "perez"), _ranked) is None
    print("ok")
