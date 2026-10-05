"""
Partner change tracking, built from the women's entry list PDFs.

data/partnerships.json holds every pair of every tournament:
    {tournament_slug, section, player_slug, player_name, partner_slug, partner_name}

A pair on a match card is a NEW PAIR when one of its players had a different
partner in the most recent previous tournament she entered.

    python partnerships.py            # rebuild data/partnerships.json and list the changes
"""

import json
import sys

from scrape_rankings import (
    _make_lookup_index, _parse_pdf_pairs, is_short_form, match_candidates, player_slug,
)
from tournaments import DATA_DIR, TOURNAMENTS_PATH, Tournament, load_tournaments, short_name

PARTNERSHIPS_PATH = DATA_DIR / "partnerships.json"
ALIASES_PATH = DATA_DIR / "player_aliases.json"


# ── Build ─────────────────────────────────────────────────────────────────────

def _load_aliases() -> dict[str, str]:
    """Hand-kept spellings no rule connects (typos, abbreviations): {variant slug: the player's usual slug}."""
    if not ALIASES_PATH.exists():
        return {}
    with open(ALIASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _canonical_slugs(names: dict[str, str]) -> dict[str, str]:
    """
    Map each slug to one canonical slug per person. The entry lists spell a name with a
    different number of words ('Marta Borrero' / 'Marta Borrero Fernández De La Puente',
    'Virginia Riera' / 'Maria Virginia Riera'): a name that is a longer one with words
    left out is the same player — when only one player fits ('Cristina Gonzalez' could be
    two, and stays apart). data/player_aliases.json connects what this rule cannot.
    """
    aliases = _load_aliases()
    words = {slug: tuple(w for w in aliases.get(slug, slug).split("-") if w) for slug in names}
    # One slug per spelling ('lopez--barajas' and an alias share the words of the usual slug)
    spelled: dict[tuple, str] = {}
    for slug in names:
        usual = "-".join(words[slug])
        spelled.setdefault(words[slug], usual if usual in names else slug)

    person: dict[tuple, tuple] = {}
    for spelling in sorted(spelled, key=len, reverse=True):   # longest first: it is nobody's short form
        longer = [other for other in person if is_short_form(spelling, other)]
        # Surnames dropped at the end is the usual case, and settles it when it fits one player
        for candidates in ([o for o in longer if o[:len(spelling)] == spelling], longer):
            fits = {person[other] for other in candidates}
            if len(fits) == 1:
                break
        person[spelling] = fits.pop() if len(fits) == 1 else spelling
    return {slug: spelled[person[words[slug]]] for slug in names}


def build_partnerships(tournaments: list[Tournament] | None = None) -> list[dict]:
    """Parse every local entry list PDF (ordered by start date) and write data/partnerships.json."""
    tournaments = tournaments or load_tournaments()
    raw = []
    names: dict[str, str] = {}
    for tournament in tournaments:
        path = tournament.local_pdf_path
        if not path.exists():
            continue
        try:
            pairs = _parse_pdf_pairs(path.read_bytes())
        except Exception as exc:
            print(f"[partners] WARNING: could not parse {path}: {exc}")
            continue
        if not pairs:
            print(f"[partners] WARNING: no pairs parsed from {path}")
        for pair in pairs:
            a, b = (p["full_name"] for p in pair["players"])
            names[player_slug(a)] = a
            names[player_slug(b)] = b
            raw.append((tournament.slug, pair["section"], player_slug(a), player_slug(b)))

    canonical = _canonical_slugs(names)
    rows = [
        {
            "tournament_slug": t_slug,
            "section":         section,
            "player_slug":     canonical[a],
            "player_name":     names[canonical[a]],
            "partner_slug":    canonical[b],
            "partner_name":    names[canonical[b]],
        }
        for t_slug, section, a, b in raw
    ]
    PARTNERSHIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PARTNERSHIPS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"[partners] {len(rows)} pairs from {len({r['tournament_slug'] for r in rows})} tournaments -> {PARTNERSHIPS_PATH}")
    return rows


def _is_stale(tournaments: list[Tournament]) -> bool:
    if not PARTNERSHIPS_PATH.exists():
        return True
    built = PARTNERSHIPS_PATH.stat().st_mtime
    sources = [TOURNAMENTS_PATH, ALIASES_PATH] + [t.local_pdf_path for t in tournaments]
    return any(p.exists() and p.stat().st_mtime > built for p in sources)


_loaded: list[dict] | None = None


def load_partnerships() -> list[dict]:
    """Load data/partnerships.json, rebuilding it when a PDF or tournaments.json is newer."""
    global _loaded
    if _loaded is None:
        tournaments = load_tournaments()
        if _is_stale(tournaments):
            _loaded = build_partnerships(tournaments)
        else:
            with open(PARTNERSHIPS_PATH, encoding="utf-8") as f:
                _loaded = json.load(f)
    return _loaded


# ── Lookups ───────────────────────────────────────────────────────────────────

def _short_player_name(full_name: str) -> str:
    """'Martina Calvo Santamaria' → 'M. Calvo Santamaria'"""
    parts = full_name.split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else full_name


def previous_partners(rows: list[dict], tournament_slug: str) -> dict[str, dict]:
    """
    For every player: her partner in the most recent tournament she entered
    before *tournament_slug* → ``{player_slug: {partner_slug, partner_name, tournament_name}}``.
    That tournament can be in an earlier season; its name then carries the year
    ('Qatar Airways Finals 2025'). Empty for the very first tournament.
    """
    tournaments = load_tournaments()
    order = [t.slug for t in tournaments]
    if tournament_slug not in order:
        return {}
    position = order.index(tournament_slug)
    season = tournaments[position].year
    earlier = {t.slug: t for t in tournaments[:position]}

    previous: dict[str, dict] = {}
    for row in sorted((r for r in rows if r["tournament_slug"] in earlier),
                      key=lambda r: order.index(r["tournament_slug"])):
        tournament = earlier[row["tournament_slug"]]
        t_name = short_name(tournament.name) + (f" {tournament.year}" if tournament.year != season else "")
        for me, other in (("player", "partner"), ("partner", "player")):
            previous[row[f"{me}_slug"]] = {
                "partner_slug":    row[f"{other}_slug"],
                "partner_name":    row[f"{other}_name"],
                "tournament_name": t_name,
            }
    return previous


class new_pair_checker:
    """
    ``checker(name1, name2)`` → tooltip text if the two widget names (e.g.
    'A. Sanchez Fallada', 'A. Ustero Prieto') are a new pair in *tournament_slug*, else None.

    Names are resolved against the tournament's own entry list. An ambiguous
    name ('A. Martinez') is settled by the listed pair it forms with the other
    player; if a player cannot be resolved, no badge is shown.
    """

    def __init__(self, rows: list[dict], tournament_slug: str):
        current = [r for r in rows if r["tournament_slug"] == tournament_slug]
        players = {}
        for row in current:
            players[row["player_slug"]] = {"full_name": row["player_name"]}
            players[row["partner_slug"]] = {"full_name": row["partner_name"]}
        self._index = _make_lookup_index(players)
        self._pairs = {frozenset((r["player_slug"], r["partner_slug"])) for r in current}
        self._previous = previous_partners(rows, tournament_slug)
        self.found: dict[frozenset, str] = {}   # new pairs seen so far → tooltip

    def _resolve(self, name1: str, name2: str) -> tuple[str, str] | None:
        cands1 = match_candidates(name1, self._index)
        cands2 = match_candidates(name2, self._index)
        listed = [(a, b) for a in cands1 for b in cands2 if frozenset((a, b)) in self._pairs]
        if len(listed) == 1:
            return listed[0]
        if len(cands1) == 1 and len(cands2) == 1 and cands1[0] != cands2[0]:
            return cands1[0], cands2[0]
        return None

    def __call__(self, name1: str, name2: str) -> str | None:
        resolved = self._resolve(name1, name2)
        if not resolved:
            return None
        lines = []
        for (slug, name), other in zip(zip(resolved, (name1, name2)), reversed(resolved)):
            prev = self._previous.get(slug)
            if prev and prev["partner_slug"] != other:
                lines.append(
                    f"{name}: previously with {_short_player_name(prev['partner_name'])}"
                    f" ({prev['tournament_name']})"
                )
        if not lines:
            return None
        if len(lines) == 1:   # only one player has a history → "Previously with …"
            lines = ["Previously" + lines[0].split(": previously", 1)[1]]
        tooltip = "\n".join(lines)
        self.found[frozenset(resolved)] = tooltip
        return tooltip


# ── CLI: rebuild + report ─────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    tournaments = load_tournaments()
    rows = build_partnerships(tournaments)
    for tournament in tournaments:
        current = [r for r in rows if r["tournament_slug"] == tournament.slug]
        if not current:
            continue
        previous = previous_partners(rows, tournament.slug)
        changes = []
        for row in current:
            for me, other in (("player", "partner"), ("partner", "player")):
                prev = previous.get(row[f"{me}_slug"])
                if prev and prev["partner_slug"] != row[f"{other}_slug"]:
                    changes.append(
                        f"    {row[f'{me}_name']} + {row[f'{other}_name']}"
                        f"  (previously with {prev['partner_name']}, {prev['tournament_name']})"
                    )
        print(f"{tournament.slug}: {len(current)} pairs, {len(changes)} player(s) with a new partner")
        for line in changes:
            print(line)


if __name__ == "__main__":
    main()
