"""Match statistics (whole match + each set) from the matchscorerlive widget.

The widget loads them on click: POST /screen/getmatchstats with the values found on the
card's "MATCH STATS" link (``a.open``: data-id, data-tid, data-year, data-org). The
endpoint sends no CORS header, so the page cannot call it — the stats are fetched here
and embedded in the page by ``generate_html.inject_match_stats``.
"""

import json
import time

import requests
from bs4 import BeautifulSoup

from scrape_matches import HEADERS, WIDGET_ORIGIN
from tournaments import Tournament

STATS_URL = WIDGET_ORIGIN + "/screen/getmatchstats?t=tol"
REQUEST_DELAY_SEC = 0.3


def stat_links(body_html: str) -> list[dict]:
    """The stats link of every started match in one day's widget HTML.

    Returns ``[{id, tid, year, org, live}]``. Matches not started yet have a link too,
    but no stats — they are left out.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    links = []
    for table in soup.find_all("table", class_="w-100"):
        link = table.find("a", class_="open")
        if not link or not link.get("data-id"):
            continue
        live = bool(table.find("img", class_="ballg"))
        if not live and not table.find("tr", class_="scorebox-header-completed"):
            continue
        links.append({
            "id":   link["data-id"],
            "tid":  link.get("data-tid", ""),   # kept as text: Gijón is "0905"
            "year": link.get("data-year", ""),
            "org":  link.get("data-org", "FIP"),
            "live": live,
        })
    return links


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_stats_html(html: str) -> dict | None:
    """Parse the widget's stats fragment; None when it holds no statistics.

    Returns::

        {"score": "3-6 4-6", "time": "01:47:34",
         "teams": [["C. FERNANDEZ SANCHEZ", "M. CALVO"], ["P. JOSEMARIA MARTIN", "B. GONZALEZ FERNANDEZ"]],
         "periods": [{"name": "Match", "sections": [{"title": "Serve", "rows": [["Aces", "0", "0"], …]}]}]}
    """
    soup = BeautifulSoup(html, "html.parser")

    periods = []
    for tab in soup.find_all("a", class_="nav-link"):
        pane = soup.find(id=tab.get("href", "").lstrip("#"))
        if not pane:
            continue
        sections = []
        for row in pane.find_all("div", class_="w-100"):
            title = row.find("div", class_="stats-data-points-section")
            if title:
                sections.append({"title": _text(title), "rows": []})
                continue
            label = row.find("div", class_="stats-data-points")
            values = row.find_all("div", class_="stats-l1-values")
            if not label or len(values) != 2:
                continue
            if not sections:
                sections.append({"title": "", "rows": []})
            sections[-1]["rows"].append([_text(label), _text(values[0]), _text(values[1])])
        sections = [s for s in sections if s["rows"]]
        if sections:
            periods.append({"name": _text(tab), "sections": sections})
    if not periods:
        return None

    # Score block: [team A] [score + duration] [team B]
    score_el = soup.find("div", class_="stats-score")
    teams: list[list[str]] = [[], []]
    if score_el:
        middle = score_el.parent
        for side, siblings in enumerate((middle.find_previous_siblings("div"), middle.find_next_siblings("div"))):
            for block in siblings:
                teams[side].extend(_text(name) for name in block.find_all("span", class_="stats-team"))
    return {
        "score":   _text(score_el),
        "time":    _text(soup.find("div", class_="stats-matchtime")),
        "teams":   teams,
        "periods": periods,
    }


def fetch_match_stats(link: dict) -> dict | None:
    """Stats of one match (a ``stat_links`` entry); None when the widget has none.

    Raises on network / server errors other than 404.
    """
    resp = requests.post(
        STATS_URL,
        data={"matchId": link["id"], "year": link["year"], "tournamentId": link["tid"], "organization": link["org"]},
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return parse_stats_html(resp.text)


class MatchStats:
    """Stats of one tournament's matches, by widget match id.

    Finished matches are fetched once and kept in ``data/cache/<slug>/stats.json``;
    live matches are fetched again on every ``update()`` and never written to disk.
    A finished match without stats is stored as ``null`` only once the tournament is
    frozen — until then it is remembered for this run only.
    """

    def __init__(self, tournament: Tournament, use_cache: bool = True):
        self.tournament = tournament
        self.path = tournament.cache_dir / "stats.json"
        self._final: dict[str, dict | None] = {}
        self._live: dict[str, dict] = {}
        self._missing: set[str] = set()
        if use_cache and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._final = json.load(f)

    def get(self, match_id: str) -> dict | None:
        return self._final.get(match_id) or self._live.get(match_id)

    def update(self, bodies) -> int:
        """Fetch what is missing for the matches in *bodies* (widget HTML of one or more days).

        Returns the number of HTTP requests made.
        """
        frozen = self.tournament.is_frozen()
        requested = 0
        dirty = False
        for body in bodies:
            if not body:
                continue
            for link in stat_links(body):
                match_id = link["id"]
                if not link["live"] and (match_id in self._final or match_id in self._missing):
                    continue
                if requested:
                    time.sleep(REQUEST_DELAY_SEC)
                requested += 1
                try:
                    stats = fetch_match_stats(link)
                except Exception as exc:
                    print(f"[stats] {match_id}: {exc}")
                    continue
                if link["live"]:
                    if stats:
                        self._live[match_id] = stats
                elif stats or frozen:
                    self._final[match_id] = stats
                    dirty = True
                else:
                    self._missing.add(match_id)
        if dirty:
            self._save()
        return requested

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._final, f, ensure_ascii=False, separators=(",", ":"))
