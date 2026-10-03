"""Generate a multi-day HTML Order of Play page using the widget's native CSS design."""

import json
import os
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


# ── Custom CSS — only rank badge + date nav (everything else comes from the widget) ──

_CSS = """\
/* ── FIP rank badge ──────────────────────────────────────────── */
.fip-rank-badge {
  display: inline-block;
  background: #f5a623;
  color: #1a2b5e;
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
a.fip-rank-badge:hover { background: #e09000; }

/* ── Date-navigation bar ─────────────────────────────────────── */
.fip-date-nav {
  background: #1a2b5e;
  display: flex !important;
  flex-wrap: nowrap !important;
  flex-shrink: 0;
  gap: 6px;
  padding: 10px 24px;
  overflow-x: auto;
  align-items: center;
}
.fip-back-link {
  color: #fff;
  font-size: .8rem;
  white-space: nowrap;
  margin-right: 10px;
  opacity: 0.75;
  text-decoration: none;
}
.fip-back-link:hover { opacity: 1; text-decoration: underline; }
.fip-nav-title {
  color: #fff;
  font-size: 1.5rem;
  font-weight: 700;
  white-space: nowrap;
  margin-right: 10px;
  opacity: 0.75;
  letter-spacing: 0.3px;
}
.fip-day-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(255,255,255,.12);
  border: none;
  border-radius: 8px;
  color: #fff;
  cursor: pointer;
  padding: 7px 13px;
  min-width: 60px;
  flex-shrink: 0;
  transition: background .15s;
  font-family: inherit;
}
.fip-day-btn:hover:not([disabled]) { background: rgba(255,255,255,.25); }
.fip-day-btn.active { background: #f5a623; color: #1a2b5e; }
.fip-day-btn[disabled] { opacity: .35; cursor: default; }
.fip-day-weekday { font-size: .74rem; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; }
.fip-day-date    { font-size: .95rem; font-weight: 600; margin-top: 2px; }

/* ── Day panel visibility ────────────────────────────────────── */
.fip-day-panel { display: none; }
.fip-day-panel.fip-active { display: block; }

/* ── Empty-day message ───────────────────────────────────────── */
.fip-empty-day {
  text-align: center;
  padding: 60px 24px;
  color: #1a2b5e;
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
  color: #aaa;
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


def generate_html(
    bodies_by_day: dict[int, str],
    tournament_dates: list[date],
    stylesheet_urls: list[str],
    output_path: str = "output/index.html",
    tournament_name: str = "FIP Tournament",
    refresh_interval: int = 60,
    fonts_dir: Path | None = None,
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
body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: #1a2b5e; background: #fff; display: flex; flex-direction: column; min-height: 100vh; }
.fip-landing-header { background: #1a2b5e; color: #fff; padding: 14px 24px; font-size: 1.5rem; font-weight: 700; }
.fip-landing-main { flex: 1; padding: 16px 24px; }
.fip-landing-table { border-collapse: collapse; width: 100%; max-width: 900px; }
.fip-landing-table th, .fip-landing-table td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #e3e6ee; white-space: nowrap; }
.fip-landing-table th { font-size: .74rem; text-transform: uppercase; letter-spacing: .8px; }
.fip-landing-table a { color: #1a2b5e; font-weight: 700; }
.fip-status { font-size: .74rem; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; }
.fip-status-ongoing { color: #c0392b; }
.fip-status-upcoming, .fip-status-postponed, .fip-status-no-data { opacity: .6; }
.fip-footer { text-align: center; padding: 14px; font-size: .72rem; color: #aaa; }
"""


def generate_landing_html(rows: list[dict], output_path: str, title: str) -> None:
    """Write the landing page listing every tournament.

    Each row: ``{name, tier, dates, status, href}`` — ``href`` is None when the
    tournament has no page (upcoming, postponed, or no data).
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    trs: list[str] = []
    for row in rows:
        name = escape(row["name"])
        if row.get("href"):
            name = f'<a href="{escape(row["href"])}">{name}</a>'
        status = row["status"]
        status_cls = "fip-status-" + status.lower().replace(" ", "-")
        trs.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{escape(row.get('tier') or '')}</td>"
            f"<td>{escape(row.get('dates') or '—')}</td>"
            f'<td><span class="fip-status {status_cls}">{escape(status)}</span></td>'
            "</tr>"
        )

    html = "".join([
        "<!DOCTYPE html>\n",
        '<html lang="en">\n',
        "<head>\n",
        '<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        f"<title>{escape(title)}</title>\n",
        f"<style>\n{_LANDING_CSS}</style>\n",
        "</head>\n",
        "<body>\n",
        f'<header class="fip-landing-header">{escape(title)}</header>\n',
        '<main class="fip-landing-main">\n',
        '<table class="fip-landing-table">\n',
        "<thead><tr><th>Tournament</th><th>Tier</th><th>Dates</th><th>Status</th></tr></thead>\n",
        "<tbody>\n",
        "\n".join(trs) + "\n",
        "</tbody>\n",
        "</table>\n",
        "</main>\n",
        '<footer class="fip-footer">Made by ice🧊 &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        "</body>\n",
        "</html>",
    ])
    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")
