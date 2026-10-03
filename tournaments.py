"""Tournament config, read from data/tournaments.json (written by discover_tournaments.py)."""

import json
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
        ))
    return sorted(tournaments, key=lambda t: t.start_date)


def get_tournament(slug: str) -> Tournament:
    tournaments = load_tournaments()
    for t in tournaments:
        if t.slug == slug:
            return t
    known = ", ".join(t.slug for t in tournaments)
    raise KeyError(f"Unknown tournament '{slug}'. Available: {known}")


def default_tournament(today: date | None = None) -> Tournament:
    """The tournament whose dates include today, else the most recent one."""
    today = today or date.today()
    tournaments = load_tournaments()
    if not tournaments:
        raise ValueError(f"No usable tournaments in {TOURNAMENTS_PATH}")
    started = [t for t in tournaments if t.start_date <= today]
    if not started:
        return tournaments[0]
    ongoing = [t for t in started if today <= t.end_date]
    return (ongoing or started)[-1]
