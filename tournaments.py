"""Tournament config, read from data/tournaments.json (written by discover_tournaments.py)."""

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
TOURNAMENTS_PATH = DATA_DIR / "tournaments.json"


@dataclass(frozen=True)
class Tournament:
    slug: str
    name: str
    tier: str | None
    tournament_id: int | str    # matchscorerlive widget ID (a string when it has a leading zero, e.g. '0905')
    year: int
    start_date: date
    total_days: int
    entry_list_pdf_url: str | None
    timezone: str | None = None   # IANA zone of the venue, e.g. "Europe/Amsterdam"
    location: str | None = None   # "Buenos aires - Argentina" (page header)
    image_url: str | None = None  # tournament poster (page header)

    @property
    def dates(self) -> list[date]:
        return [self.start_date + timedelta(days=i) for i in range(self.total_days)]

    @property
    def end_date(self) -> date:
        return self.dates[-1]

    def status(self, today: date | None = None) -> str:
        """finished / ongoing / upcoming, by calendar date."""
        today = today or date.today()
        if today > self.end_date:
            return "finished"
        if today < self.start_date:
            return "upcoming"
        return "ongoing"

    # The last day is given one extra calendar day before it counts as over:
    # a final played in the Americas can still be live after local midnight here.
    def is_refreshable(self, today: date | None = None) -> bool:
        """True while the schedule can still change (watch mode re-fetches it)."""
        today = today or date.today()
        return self.start_date <= today <= self.end_date + timedelta(days=1)

    def is_frozen(self, today: date | None = None) -> bool:
        """True once the tournament is over for good — its data is fetched once and cached."""
        today = today or date.today()
        return today > self.end_date + timedelta(days=1)

    @property
    def cache_dir(self) -> Path:
        return DATA_DIR / "cache" / self.slug

    @property
    def local_pdf_path(self) -> Path:
        return DATA_DIR / "pdfs" / self.slug / "entry_list_women.pdf"


def short_name(name: str) -> str:
    """'Premier Padel Buenos Aires P1 2026' / 'BNL Italy Major Premier Padel 2023' → 'Buenos Aires P1' / 'BNL Italy Major'.

    The series and the season are dropped wherever they stand in the name; a name
    that is nothing more ('Premier Padel Finals 2026') keeps the series.
    """
    no_year = " ".join(re.sub(r"\b20\d{2}\b", " ", name).split())
    short = " ".join(re.sub(r"\bPremier Padel\b", " ", no_year, flags=re.I).split())
    return short if " " in short else no_year or name


def load_entries() -> list[dict]:
    """Raw entries of data/tournaments.json, by start date (undated ones last)."""
    if not TOURNAMENTS_PATH.exists():
        raise FileNotFoundError(
            f"{TOURNAMENTS_PATH} not found — run: python discover_tournaments.py --year <year>"
        )
    with open(TOURNAMENTS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return sorted(raw, key=lambda t: (t.get("start_date") is None, t.get("start_date") or ""))


def load_tournaments() -> list[Tournament]:
    """Load every tournament that has an ID and dates (postponed ones are skipped)."""
    tournaments = []
    for t in load_entries():
        if not (t.get("tournament_id") and t.get("start_date") and t.get("total_days")):
            continue
        tournaments.append(Tournament(
            slug=t["slug"],
            name=t.get("name") or t["slug"],
            tier=t.get("tier"),
            tournament_id=t["tournament_id"],
            year=t["year"],
            start_date=date.fromisoformat(t["start_date"]),
            total_days=t["total_days"],
            entry_list_pdf_url=t.get("entry_list_pdf_url"),
            timezone=t.get("timezone"),
            location=t.get("location"),
            image_url=t.get("image_url"),
        ))
    return sorted(tournaments, key=lambda t: t.start_date)


def get_tournament(slug: str) -> Tournament:
    tournaments = load_tournaments()
    for t in tournaments:
        if t.slug == slug:
            return t
    known = ", ".join(t.slug for t in tournaments)
    raise KeyError(f"Unknown tournament '{slug}'. Available: {known}")


def default_candidates(today: date | None = None) -> list[Tournament]:
    """Tournaments to try as the default, best first.

    Those whose dates include today (latest start first), then the most recent one
    already finished. Two tournaments overlap when qualifying of the next one starts
    on the day of a final — and that first day may have no women's matches yet.
    """
    today = today or date.today()
    tournaments = load_tournaments()
    if not tournaments:
        raise ValueError(f"No usable tournaments in {TOURNAMENTS_PATH}")
    started = [t for t in tournaments if t.start_date <= today]
    if not started:
        return tournaments[:1]
    ongoing = [t for t in started if today <= t.end_date]
    finished = [t for t in started if t.end_date < today]
    return ongoing[::-1] + finished[-1:]


def default_tournament(today: date | None = None) -> Tournament:
    """The tournament whose dates include today, else the most recent one."""
    return default_candidates(today)[0]
