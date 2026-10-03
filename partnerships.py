"""
Partner change tracking, built from the women's entry list PDFs.

data/partnerships.json holds every pair of every tournament:
    {tournament_slug, section, player_slug, player_name, partner_slug, partner_name}

A pair on a match card is a NEW PAIR when one of its players had a different
partner in the most recent previous tournament she entered.

    python partnerships.py            # rebuild data/partnerships.json and list the changes
"""

import json
import re
import sys

from scrape_rankings import (
    _make_lookup_index, _parse_pdf_pairs, match_candidates, player_slug,
)
from tournaments import DATA_DIR, TOURNAMENTS_PATH, Tournament, load_tournaments

PARTNERSHIPS_PATH = DATA_DIR / "partnerships.json"


# ── Build ─────────────────────────────────────────────────────────────────────

def _canonical_slugs(names: dict[str, str]) -> dict[str, str]:
    """
    Map each slug to one canonical slug per person. PDFs spell some names with a
    different number of surnames ('Marta Borrero Fernandez' / '… Fernandez De La Puente'):
    a name whose words are a prefix of a longer name is the same player.
    """
    canonical = {}
    for slug in names:
        parts = slug.split("-")
        longer = [
            other for other in names
            if other != slug and len(parts) >= 2 and other.split("-")[:len(parts)] == parts
        ]
        canonical[slug] = max(longer, key=len) if len(longer) == 1 else slug
    return canonical


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
    sources = [TOURNAMENTS_PATH] + [t.local_pdf_path for t in tournaments]
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

def _short_tournament_name(name: str) -> str:
    """'Premier Padel Buenos Aires P1 2026' → 'Buenos Aires P1'"""
    short = re.sub(r"\bPremier Padel\b|\b20\d{2}\b", " ", name)
    return " ".join(short.split()) or name


def _short_player_name(full_name: str) -> str:
    """'Martina Calvo Santamaria' → 'M. Calvo Santamaria'"""
    parts = full_name.split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else full_name


def previous_partners(rows: list[dict], tournament_slug: str) -> dict[str, dict]:
    """
    For every player: her partner in the most recent tournament she entered
    before *tournament_slug* → ``{player_slug: {partner_slug, partner_name, tournament_name}}``.
    Empty for the first tournament of the season.
    """
    tournaments = load_tournaments()
    order = [t.slug for t in tournaments]
    if tournament_slug not in order:
        return {}
    earlier = {t.slug: t for t in tournaments[:order.index(tournament_slug)]}

    previous: dict[str, dict] = {}
    for row in sorted((r for r in rows if r["tournament_slug"] in earlier),
                      key=lambda r: order.index(r["tournament_slug"])):
        t_name = _short_tournament_name(earlier[row["tournament_slug"]].name)
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
