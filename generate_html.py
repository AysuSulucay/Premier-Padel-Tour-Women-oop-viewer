"""Generate a multi-day HTML Order of Play page using the widget's native CSS design."""

from datetime import date, datetime, timezone, timedelta
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


def _ensure_fonts(output_dir: Path) -> bool:
    """Download DINPro font files next to the output HTML (output/fonts/).

    Returns True if all fonts are available locally, False if any failed.
    Skips files that already exist (idempotent).
    """
    fonts_dir = output_dir / "fonts"
    fonts_dir.mkdir(exist_ok=True)
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


def _font_face_css(fonts_available: bool) -> str:
    """Return @font-face declarations pointing to local font files."""
    if not fonts_available:
        return ""
    lines = []
    for filename, (family, weight) in _FONT_FILES.items():
        lines.append(
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  src: url('./fonts/{filename}') format('woff');\n"
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

  /* ── Live Buenos Aires clock (UTC-3, no DST) ────────────────── */
  function artTime() {
    var now = new Date();
    // Shift to ART = UTC-3
    var art = new Date(now.getTime() + now.getTimezoneOffset() * 60000 - 3 * 3600000);
    var h = art.getHours(), m = art.getMinutes();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return h + ':' + (m < 10 ? '0' : '') + m + ' ' + ampm;
  }
  function updateClocks() {
    var t = artTime();
    document.querySelectorAll('.local-time').forEach(function (el) { el.textContent = t; });
  }
  updateClocks();
  setInterval(updateClocks, 30000); // re-render every 30 s (display changes per minute)

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
    """
    # Buenos Aires time — Argentina does not observe DST (always UTC-3)
    _ART = timezone(timedelta(hours=-3))
    generated_at = datetime.now(_ART).strftime("%H:%M")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Download DINPro fonts locally so they load without CORS issues
    fonts_ok = _ensure_fonts(out.parent)
    font_face_css = _font_face_css(fonts_ok)

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
        f'  <span class="fip-nav-title">{escape(tournament_name)}</span>\n',
    ]
    for btn in nav_btns:
        parts.append(f"  {btn}\n")
    parts.append("</nav>\n")
    parts.append('<div id="fip-panels-wrapper">\n')
    for panel in panels:
        parts.append(panel + "\n")
    parts.append('</div>\n')
    parts.extend([
        f'<footer class="fip-footer">Made by ice🧊 &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        f"<script>\n{_JS}</script>\n",
        "</body>\n",
        "</html>",
    ])

    html = "".join(parts)

    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")
