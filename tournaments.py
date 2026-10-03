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
    tournament_id: int          # matchscorerlive widget ID
    year: int
    start_date: date
    total_days: int
    entry_list_pdf_url: str | None

    @property
    def dates(self) -> list[date]:
        return [self.start_date + timedelta(days=i) for i in range(self.total_days)]

    @property
    def end_date(self) -> date:
        return self.dates[-1]

    @property
    def cache_dir(self) -> Path:
        return DATA_DIR / "cache" / self.slug

    @property
    def local_pdf_path(self) -> Path:
        return DATA_DIR / "pdfs" / self.slug / "entry_list_women.pdf"


def load_tournaments() -> list[Tournament]:
    """Load every tournament that has an ID and dates (postponed ones are skipped)."""
    if not TOURNAMENTS_PATH.exists():
        raise FileNotFoundError(
            f"{TOURNAMENTS_PATH} not found — run: python discover_tournaments.py --year <year>"
        )
    with open(TOURNAMENTS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    tournaments = []
    for t in raw:
        if not (t.get("tournament_id") and t.get("start_date") and t.get("total_days")):
            continue
        tournaments.append(Tournament(
            slug=t["slug"],
            name=t.get("name") or t["slug"],
            tournament_id=t["tournament_id"],
            year=t["year"],
            start_date=date.fromisoformat(t["start_date"]),
            total_days=t["total_days"],
            entry_list_pdf_url=t.get("entry_list_pdf_url"),
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
