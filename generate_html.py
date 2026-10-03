"""Generate a multi-day HTML Order of Play page using the widget's native CSS design."""

import json
import os
import shutil
from datetime import date
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from scrape_rankings import match_player

# ── DINPro font files served from widget (blocked cross-origin — downloaded locally) ───
_FONT_BASE_URL = "https://widget.matchscorerlive.com/css/app/"
_FONT_FILES = {
    "DINPro-CondensedRegular.woff": ("Din Condensed", 400),
    "DINPro.woff":                  ("Din",            400),
    "DINPro-Black.woff":            ("Din",            900),
}


def _ensure_fonts(fonts_dir: Path) -> bool:
    """Download DINPro font files into *fonts_dir* (e.g. output/fonts/).

    Returns True if all fonts are available locally, False if any failed.
    Skips files that already exist (idempotent).
    """
    fonts_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for filename in _FONT_FILES:
        dest = fonts_dir / filename
        if dest.exists():
            continue
        url = _FONT_BASE_URL + filename
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            dest.write_bytes(r.content)
            print(f"[fonts] Downloaded {filename} ({len(r.content):,} bytes)")
        except Exception as exc:
            print(f"[fonts] WARNING: could not download {filename}: {exc}")
            all_ok = False
    return all_ok


def _font_face_css(fonts_available: bool, fonts_href: str = "./fonts") -> str:
    """Return @font-face declarations pointing to local font files."""
    if not fonts_available:
        return ""
    lines = []
    for filename, (family, weight) in _FONT_FILES.items():
        lines.append(
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  src: url('{fonts_href}/{filename}') format('woff');\n"
            f"  font-weight: {weight};\n"
            f"  font-style: normal;\n"
            f"}}"
        )
    return "\n".join(lines) + "\n"


# ── Design tokens (output/assets/theme.css) + the web fonts they name ─────────
_THEME_FILE = "theme.css"
_THEME_SOURCE = Path(__file__).parent / "output" / "assets" / _THEME_FILE
_WEBFONTS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Barlow+Condensed:wght@600;700&family=DM+Sans:wght@400;500;700&display=swap"
)


def _theme_link_tags(out: Path, assets_dir: Path | None = None) -> str:
    """Return the <link> tags for the web fonts and theme.css, relative to *out*.

    *assets_dir* defaults to ``assets/`` next to the output file. When that is not
    ``output/assets/`` (standalone ``--output`` page), theme.css is copied there.
    """
    assets_dir = Path(assets_dir) if assets_dir else out.parent / "assets"
    dest = assets_dir / _THEME_FILE
    if dest.resolve() != _THEME_SOURCE.resolve():
        assets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_THEME_SOURCE, dest)
    href = os.path.relpath(dest.resolve(), out.parent.resolve()).replace(os.sep, "/")
    return (
        f'<link rel="stylesheet" href="{escape(_WEBFONTS_URL)}">\n'
        f'<link rel="stylesheet" href="{escape(href)}">'
    )


# ── Custom CSS — only rank badge + date nav (everything else comes from the widget) ──
# Colors, fonts and spacing are var(--…) tokens from theme.css — no literal colors here.

_CSS = """\
/* ── FIP rank badge ──────────────────────────────────────────── */
.fip-rank-badge {
  display: inline-block;
  background: var(--color-accent-2);
  color: var(--color-bg);
  font-size: 0.66rem;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 4px;
  text-decoration: none;
  white-space: nowrap;
  margin-left: 6px;
  vertical-align: middle;
  line-height: 1.5;
}
a.fip-rank-badge:hover { background: var(--color-highlight); }

/* ── NEW PAIR badge ──────────────────────────────────────────── */
.fip-new-pair {
  display: inline-block;
  background: var(--color-surface-2);
  color: var(--color-highlight);
  font-size: 0.6rem;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 4px;
  white-space: nowrap;
  margin-right: 6px;
  vertical-align: middle;
  line-height: 1.5;
  cursor: help;
}

/* ── Date-navigation bar ─────────────────────────────────────── */
.fip-date-nav {
  background: var(--color-surface);
  font-family: var(--font-body);
  display: flex !important;
  flex-wrap: nowrap !important;
  flex-shrink: 0;
  gap: 6px;
  padding: 10px var(--space-4);
  overflow-x: auto;
  align-items: center;
}
.fip-back-link {
  color: var(--color-accent-2);
  font-size: .8rem;
  white-space: nowrap;
  margin-right: 10px;
  text-decoration: none;
}
.fip-back-link:hover { color: var(--color-highlight); text-decoration: underline; }
.fip-nav-title {
  color: var(--color-text);
  font-family: var(--font-heading);
  font-size: 1.5rem;
  font-weight: 700;
  white-space: nowrap;
  margin-right: 10px;
  letter-spacing: 0.3px;
}
.fip-day-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--color-surface-2);
  border: none;
  border-radius: 8px;
  color: var(--color-highlight);
  cursor: pointer;
  padding: 7px 13px;
  min-width: 60px;
  flex-shrink: 0;
  transition: background .15s;
  font-family: inherit;
}
.fip-day-btn:hover:not([disabled]) { background: var(--color-accent-hover); }
.fip-day-btn.active, .fip-day-btn.active:hover:not([disabled]) { background: var(--color-accent); color: var(--color-text); }
.fip-day-btn[disabled] { opacity: .35; cursor: default; }
.fip-day-weekday { font-size: .74rem; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; }
.fip-day-date    { font-size: .95rem; font-weight: 600; margin-top: 2px; }

/* ── Day panel visibility ────────────────────────────────────── */
.fip-day-panel { display: none; }
.fip-day-panel.fip-active { display: block; }

/* ── Empty-day message ───────────────────────────────────────── */
.fip-empty-day {
  text-align: center;
  padding: 60px var(--space-4);
  color: var(--color-bg);  /* match area stays light until the D4 overrides */
  font-family: var(--font-heading);
  font-size: 2rem;
  font-weight: 900;
  font-style: normal;
  text-transform: uppercase;
  letter-spacing: 1px;
}

/* ── Sticky footer layout ────────────────────────────────────── */
body { display: flex; flex-direction: column; min-height: 100vh; margin: 0; }
#fip-panels-wrapper { flex: 1; }

/* ── Footer ──────────────────────────────────────────────────── */
.fip-footer {
  text-align: center;
  padding: 14px;
  font-size: .72rem;
  font-family: var(--font-body);
  background: var(--color-bg);
  color: var(--color-text-muted);
}
"""

_JS = """\
(function () {

  /* ── Date-nav tab switching ─────────────────────────────────── */
  document.querySelectorAll('.fip-day-btn:not([disabled])').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.fip-day-btn').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.fip-day-panel').forEach(function (p) { p.classList.remove('fip-active'); });
      btn.classList.add('active');
      var panel = document.getElementById('fip-day-' + btn.dataset.day);
      if (panel) panel.classList.add('fip-active');
    });
  });

  /* ── Live venue clock (tournament's own time zone, DST handled by the browser) ── */
  var TIMEZONE = __FIP_TIMEZONE__;
  function venueTime() {
    return new Date().toLocaleTimeString('en-US', {
      timeZone: TIMEZONE, hour: 'numeric', minute: '2-digit', hour12: true
    });
  }
  function updateClocks() {
    var t;
    try { t = venueTime(); } catch (e) { return; }  // unknown zone → keep the widget's text
    document.querySelectorAll('.local-time').forEach(function (el) { el.textContent = t; });
  }
  if (TIMEZONE) {
    updateClocks();
    setInterval(updateClocks, 30000); // re-render every 30 s (display changes per minute)
  }

}());
"""


def inject_rank_badges(body_html: str, cache: dict, index: dict) -> str:
    """Inject FIP rank badge tags next to every player name in widget body HTML.

    Finds each ``div.line-thin`` (the widget's player-name container), reads
    the first-initial span + last-name span, looks up the player in the
    rankings cache, and appends a coloured badge element.

    Args:
        body_html: inner HTML of the widget ``<body>``, already gender-filtered.
        cache: ``{slug: {rank, full_name, profile_url, …}}`` from rankings.
        index: lookup index built by ``build_lookup_index(cache)``.

    Returns:
        Modified HTML string with rank badges appended inside each name div.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    for name_div in soup.find_all("div", class_="line-thin"):
        spans = name_div.find_all("span")
        if len(spans) < 2:
            continue
        first_initial = spans[0].get_text(strip=True)
        last_name = spans[1].get_text(strip=True)
        widget_name = f"{first_initial} {last_name}".strip()

        result = match_player(widget_name, cache, index)

        # Only inject a badge when we have a confirmed rank — never show N/A
        if result and result.get("rank") is not None:
            rank = result["rank"]
            url = result.get("profile_url", "")
            badge = soup.new_tag("a", target="_blank", rel="noopener")
            badge["href"] = url if url else "#"
            badge["class"] = "fip-rank-badge"
            badge.string = f"FIP #{rank}"
            name_div.append(badge)

    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    return body_el.decode_contents() if body_el else str(soup)


def inject_new_pair_badges(body_html: str, pair_info) -> str:
    """Add a small ``NEW PAIR`` badge next to every team that is a new partnership.

    Args:
        body_html: inner HTML of the widget ``<body>``, already gender-filtered.
        pair_info: ``callable(name1, name2) -> tooltip | None`` — the tooltip text
                   (e.g. "Previously with M. Calvo (Buenos Aires P1)") when the two
                   widget names form a new pair, else None.

    Returns:
        Modified HTML string.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    for team_td in soup.find_all("td", class_="team"):
        name_divs = team_td.find_all("div", class_="line-thin")
        if len(name_divs) != 2:
            continue
        names = []
        for name_div in name_divs:
            spans = name_div.find_all("span")
            if len(spans) < 2:
                break
            names.append(f"{spans[0].get_text(strip=True)} {spans[1].get_text(strip=True)}".strip())
        if len(names) != 2:
            continue

        tooltip = pair_info(names[0], names[1])
        if not tooltip:
            continue
        badge = soup.new_tag("span", title=tooltip)
        badge["class"] = "fip-new-pair"
        badge.string = "NEW PAIR"
        # Right-hand cell of the team row (where the winner check mark sits)
        side = team_td.find("div", class_="mr-2")
        if side:
            side.insert(0, badge)
        else:
            name_divs[0].append(badge)

    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    return body_el.decode_contents() if body_el else str(soup)


def generate_html(
    bodies_by_day: dict[int, str],
    tournament_dates: list[date],
    stylesheet_urls: list[str],
    output_path: str = "output/index.html",
    tournament_name: str = "FIP Tournament",
    refresh_interval: int = 60,
    fonts_dir: Path | None = None,
    assets_dir: Path | None = None,
    back_href: str | None = None,
    timezone_name: str | None = None,
) -> None:
    """Write a multi-day HTML Order of Play page to *output_path*.

    Uses the widget's native CSS (loaded via *stylesheet_urls*) for all match
    styling.  Only adds FIP rank badge CSS and the date-navigation bar as
    custom elements on top.

    Args:
        bodies_by_day:   ``{day_num: widget_body_html}`` — Women-only, badges injected.
        tournament_dates: ``[date(2026,5,10), …, date(2026,5,17)]``
        stylesheet_urls: CSS URLs scraped from the widget ``<head>``.
        output_path:     Where to write the output HTML file.
        tournament_name: Shown in ``<title>`` and the date-nav header.
        refresh_interval: Browser auto-reload interval in seconds (default 60;
                          reduced to 15 automatically when a live match is detected).
        fonts_dir:       Where the DINPro fonts live (default: ``fonts/`` next to the output).
        assets_dir:      Where theme.css lives (default: ``assets/`` next to the output).
        back_href:       If set, a small "← All tournaments" link to this URL is added to the nav.
        timezone_name:   IANA zone of the venue (e.g. ``"Europe/Amsterdam"``) for the live
                         "Local Time" clock. None leaves the widget's own text untouched.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Download DINPro fonts locally so they load without CORS issues
    fonts_dir = Path(fonts_dir) if fonts_dir else out.parent / "fonts"
    fonts_ok = _ensure_fonts(fonts_dir)
    fonts_href = os.path.relpath(fonts_dir.resolve(), out.parent.resolve()).replace(os.sep, "/")
    font_face_css = _font_face_css(fonts_ok, fonts_href if fonts_href.startswith(".") else f"./{fonts_href}")

    # Default active day: highest day number with actual content
    active_day = 1
    for day_num in sorted(bodies_by_day.keys()):
        if bodies_by_day[day_num]:
            active_day = day_num

    # <link> tags for widget stylesheets
    link_tags = "\n".join(
        f'<link rel="stylesheet" href="{escape(url)}">'
        for url in stylesheet_urls
    )

    # Date-nav buttons
    nav_btns: list[str] = []
    for day_num, d in enumerate(tournament_dates, start=1):
        is_active = day_num == active_day
        weekday = d.strftime("%a")
        date_str = d.strftime("%b") + " " + str(d.day)
        btn_cls = "fip-day-btn" + (" active" if is_active else "")
        nav_btns.append(
            f'<button class="{btn_cls}" data-day="{day_num}">'
            f'<span class="fip-day-weekday">{weekday}</span>'
            f'<span class="fip-day-date">{date_str}</span>'
            f'</button>'
        )

    # Day panels
    panels: list[str] = []
    for day_num, d in enumerate(tournament_dates, start=1):
        body = bodies_by_day.get(day_num, "")
        active_cls = " fip-active" if day_num == active_day else ""

        if not body:
            # Future date or fetch error — no schedule published
            content = '<p class="fip-empty-day">No Schedule Available</p>'
        elif not BeautifulSoup(body, "html.parser").find("table", class_="w-100"):
            # Widget returned data but zero Women's match tables after gender filter
            content = '<p class="fip-empty-day">No Women Matches</p>'
        else:
            content = body

        panels.append(
            f'<div class="fip-day-panel{active_cls}" id="fip-day-{day_num}">\n'
            f'{content}\n'
            f'</div>'
        )

    parts: list[str] = [
        "<!DOCTYPE html>\n",
        '<html lang="en">\n',
        "<head>\n",
        '<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        f'<meta http-equiv="refresh" content="{refresh_interval}">\n',
        f"<title>Order of Play — {escape(tournament_name)}</title>\n",
        f"{link_tags}\n",
        f"{_theme_link_tags(out, assets_dir)}\n",
        f"<style>\n{font_face_css}{_CSS}</style>\n",
        "</head>\n",
        "<body>\n",
        '<nav class="fip-date-nav">\n',
    ]
    if back_href:
        parts.append(f'  <a class="fip-back-link" href="{escape(back_href)}">← All tournaments</a>\n')
    parts.append(f'  <span class="fip-nav-title">{escape(tournament_name)}</span>\n')
    for btn in nav_btns:
        parts.append(f"  {btn}\n")
    parts.append("</nav>\n")
    parts.append('<div id="fip-panels-wrapper">\n')
    for panel in panels:
        parts.append(panel + "\n")
    parts.append('</div>\n')
    parts.extend([
        f'<footer class="fip-footer">Made by ice🧊 &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        f"<script>\n{_JS.replace('__FIP_TIMEZONE__', json.dumps(timezone_name))}</script>\n",
        "</body>\n",
        "</html>",
    ])

    html = "".join(parts)

    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")



# ── Landing page (list of tournaments) ────────────────────────────────────────

_LANDING_CSS = """\
body { margin: 0; font-family: var(--font-body); color: var(--color-text); background: var(--color-bg); display: flex; flex-direction: column; min-height: 100vh; }
.fip-landing-header { border-bottom: 1px solid var(--color-border); }
.fip-landing-header-inner, .fip-landing-main { box-sizing: border-box; width: 100%; max-width: 1200px; margin: 0 auto; }
.fip-landing-header-inner { padding: 20px var(--space-4); display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; justify-content: space-between; }
.fip-brand { font-family: var(--font-heading); font-weight: 700; font-size: 26px; letter-spacing: .5px; text-transform: uppercase; }
.fip-brand span { color: var(--color-accent-2); }
.fip-tagline { font-size: 14px; color: var(--color-text-muted); }
.fip-landing-main { flex: 1; padding: calc(var(--space-4) * 2) var(--space-4) calc(var(--space-4) * 2 + var(--space-3)); }
.fip-eyebrow { font-size: 13px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: var(--color-accent-2); }
.fip-landing-title { margin: var(--space-1) 0; font-family: var(--font-heading); font-weight: 700; font-size: 56px; line-height: 1; text-transform: uppercase; }
.fip-landing-intro { margin: 0 0 calc(var(--space-4) + var(--space-3)); font-size: 17px; color: var(--color-text-muted); }

/* ── Tournament cards ────────────────────────────────────────── */
.fip-card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--space-4); }
.fip-card {
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  color: var(--color-text);
  text-decoration: none;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
a.fip-card:hover, a.fip-card:focus-visible { transform: translateY(-6px); box-shadow: var(--shadow-card-hover); border-color: var(--color-accent); outline: none; }
.fip-card-poster { position: relative; aspect-ratio: 4 / 5; overflow: hidden; background: var(--color-surface-2); }
.fip-card-photo { position: absolute; inset: 0; width: 100%; height: 100%; transition: transform .2s ease; }
img.fip-card-photo { object-fit: cover; object-position: top; }
.fip-card-fallback { display: flex; align-items: center; justify-content: center; background: var(--color-surface-2); }
.fip-card-fallback svg { stroke: var(--color-accent-hover); }
a.fip-card:hover .fip-card-photo, a.fip-card:focus-visible .fip-card-photo { transform: scale(1.06); }
.fip-card-noposter { position: absolute; right: var(--space-2); bottom: 10px; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: var(--color-text-muted); }
.fip-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; padding: 4px 10px; border-radius: 999px; border: 1px solid transparent; white-space: nowrap; }
.fip-card-tier { position: absolute; left: var(--space-2); top: var(--space-2); background: var(--color-accent); color: var(--color-text); }
.fip-card-body { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 18px 20px 20px; }
.fip-card-name { font-family: var(--font-heading); font-weight: 700; font-size: 26px; line-height: 1.05; text-transform: uppercase; }
.fip-card-meta { margin-top: auto; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-1) var(--space-2); }
.fip-card-dates { font-size: 14px; color: var(--color-text-muted); }

/* ── Status pill ─────────────────────────────────────────────── */
.fip-status-finished { background: color-mix(in srgb, var(--color-highlight) 12%, transparent); color: var(--color-highlight); }
.fip-status-ongoing { background: var(--color-highlight); border-color: var(--color-highlight); color: var(--color-bg); }
.fip-status-ongoing::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--color-bg); }
.fip-status-upcoming { border-color: var(--color-accent); color: var(--color-accent-2); }
.fip-status-postponed, .fip-status-no-data, .fip-status-unknown { border-color: var(--color-border); color: var(--color-text-muted); }

.fip-footer { text-align: center; padding: 14px; font-size: .72rem; color: var(--color-text-muted); }
"""

# Shown in the 4:5 poster box when a tournament has no image (and under one that fails to load)
_COURT_SVG = (
    '<svg width="120" height="200" viewBox="0 0 120 220" fill="none" stroke-width="2" aria-hidden="true">'
    '<rect x="4" y="4" width="112" height="212" rx="2"></rect>'
    '<line x1="4" y1="110" x2="116" y2="110"></line>'
    '<line x1="4" y1="40" x2="116" y2="40"></line>'
    '<line x1="4" y1="180" x2="116" y2="180"></line>'
    '<line x1="60" y1="40" x2="60" y2="180"></line>'
    "</svg>"
)

_STATUS_LABELS = {"Ongoing": "Live"}


def _card_name(name: str, year) -> str:
    """'Premier Padel Buenos Aires P1 2026' → 'Buenos Aires P1' (series and year are in the page heading)."""
    short = name.removesuffix(f" {year}") if year else name
    rest = short.removeprefix("Premier Padel ")
    return rest if " " in rest else short   # keep 'Premier Padel Finals'


def generate_landing_html(rows: list[dict], output_path: str, title: str, year: int | None = None) -> None:
    """Write the landing page: one card per tournament.

    Each row: ``{name, tier, dates, status, href, image}`` — ``href`` is None when the
    tournament has no page (upcoming, postponed, or no data); ``image`` is the poster
    URL, or None for the court-drawing fallback.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    for row in rows:
        name = escape(_card_name(row["name"], year))
        status = row["status"]
        status_cls = "fip-status-" + status.lower().replace(" ", "-")
        tier = row.get("tier")
        image = row.get("image")

        poster = [f'<div class="fip-card-photo fip-card-fallback">{_COURT_SVG}</div>']
        if image:
            # A poster that fails to load removes itself, leaving the court drawing
            poster.append(
                f'<img class="fip-card-photo" src="{escape(image)}" alt="{name} poster" '
                'loading="lazy" onerror="this.remove()">'
            )
        else:
            poster.append('<div class="fip-card-noposter">No poster</div>')
        if tier:
            poster.append(f'<span class="fip-pill fip-card-tier">{escape(tier)}</span>')

        inner = (
            f'<div class="fip-card-poster">{"".join(poster)}</div>'
            '<div class="fip-card-body">'
            f'<div class="fip-card-name">{name}</div>'
            '<div class="fip-card-meta">'
            f'<span class="fip-card-dates">{escape(row.get("dates") or "—")}</span>'
            f'<span class="fip-pill {status_cls}">{escape(_STATUS_LABELS.get(status, status))}</span>'
            "</div>"
            "</div>"
        )
        if row.get("href"):
            cards.append(f'<a class="fip-card" href="{escape(row["href"])}">{inner}</a>')
        else:
            cards.append(f'<div class="fip-card">{inner}</div>')

    html = "".join([
        "<!DOCTYPE html>\n",
        '<html lang="en">\n',
        "<head>\n",
        '<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        f"<title>{escape(title)}</title>\n",
        f"{_theme_link_tags(out)}\n",
        f"<style>\n{_LANDING_CSS}</style>\n",
        "</head>\n",
        "<body>\n",
        '<header class="fip-landing-header"><div class="fip-landing-header-inner">\n',
        '<div class="fip-brand">Padel <span>Women</span></div>\n',
        '<div class="fip-tagline">FIP rankings next to every player</div>\n',
        "</div></header>\n",
        '<main class="fip-landing-main">\n',
        '<div class="fip-eyebrow">Premier Padel</div>\n',
        f'<h1 class="fip-landing-title">{escape(f"{year} Season" if year else title)}</h1>\n',
        '<p class="fip-landing-intro">Pick a tournament to see the women\'s order of play, scores and FIP ranks.</p>\n',
        '<div class="fip-card-grid">\n',
        "\n".join(cards) + "\n",
        "</div>\n",
        "</main>\n",
        '<footer class="fip-footer">Made by ice🧊 &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        "</body>\n",
        "</html>",
    ])
    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")
