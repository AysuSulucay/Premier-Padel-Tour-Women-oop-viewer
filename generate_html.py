"""Generate a multi-day HTML Order of Play page using the widget's native CSS design."""

import json
import os
import re
import shutil
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from scrape_rankings import match_player
from tournaments import short_name

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


# ── Shared stylesheets in output/assets/ + the web fonts the tokens name ──────
_ASSETS_SOURCE = Path(__file__).parent / "output" / "assets"
_THEME_FILE = "theme.css"          # design tokens (CSS variables only)
_OVERRIDES_FILE = "overrides.css"  # match-card restyling, scoped under .fip-theme
_LOGO_FILE = "premier-padel-logo.svg"   # landing header logo (white lettering)
_CALENDAR_URL = "https://www.padelfip.com/calendar-premier-padel/"   # the logo links here
_WEBFONTS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Barlow+Condensed:wght@600;700&family=DM+Sans:wght@400;500;700"
    "&family=Noto+Color+Emoji&display=swap"
)


def _theme_link_tags(out: Path, assets_dir: Path | None = None, files: tuple = (_THEME_FILE,)) -> str:
    """Return the <link> tags for the web fonts and the stylesheets in *files*, relative to *out*.

    *assets_dir* defaults to ``assets/`` next to the output file. When that is not
    ``output/assets/`` (standalone ``--output`` page), the stylesheets are copied there.
    """
    assets_dir = Path(assets_dir) if assets_dir else out.parent / "assets"
    tags = [f'<link rel="stylesheet" href="{escape(_WEBFONTS_URL)}">']
    for filename in files:
        dest = assets_dir / filename
        if dest.resolve() != (_ASSETS_SOURCE / filename).resolve():
            assets_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_ASSETS_SOURCE / filename, dest)
        href = os.path.relpath(dest.resolve(), out.parent.resolve()).replace(os.sep, "/")
        tags.append(f'<link rel="stylesheet" href="{escape(href)}">')
    return "\n".join(tags)


# ── Custom CSS — rank badge, header, date nav (match cards: widget CSS + overrides.css) ──
# Colors, fonts and spacing are var(--…) tokens from theme.css — no literal colors here.

_CSS = """\
/* ── FIP rank badge ──────────────────────────────────────────── */
.fip-rank-badge {
  display: inline-block;
  background: var(--color-accent-2);
  color: var(--color-bg);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .5px;
  padding: 2px var(--space-1);
  border-radius: 999px;
  text-decoration: none;
  white-space: nowrap;
  margin-left: 6px;
  vertical-align: middle;
  line-height: 1.5;
  transition: background .15s ease;
}
a.fip-rank-badge:hover { background: var(--color-highlight); color: var(--color-bg); text-decoration: none; }

/* ── NEW PAIR badge: soft pill + link icon, details card on hover / focus ── */
.fip-new-pair {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: var(--space-1);
  padding: 4px 10px;
  background: color-mix(in srgb, var(--color-accent) 22%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-accent-2) 35%, transparent);
  border-radius: 999px;
  color: var(--color-highlight);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  line-height: 1.5;
  text-transform: uppercase;
  white-space: nowrap;
  cursor: default;
  transition: background .15s ease, border-color .15s ease;
}
.fip-new-pair svg { flex-shrink: 0; }
.fip-new-pair:hover, .fip-new-pair:focus {
  background: color-mix(in srgb, var(--color-accent) 38%, transparent);
  border-color: var(--color-accent-2);
  outline: none;
}
.fip-new-pair-tip {
  position: absolute;
  top: calc(100% + 10px);
  left: 0;
  z-index: 20;
  box-sizing: border-box;
  width: 230px;
  padding: var(--space-2) 14px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-accent);
  border-radius: 12px;
  box-shadow: 0 14px 34px color-mix(in srgb, var(--color-bg) 70%, transparent);
  letter-spacing: 0;
  text-align: left;
  text-transform: none;
  white-space: normal;
  opacity: 0;
  transform: translateY(-4px);
  pointer-events: none;
  transition: opacity .15s ease, transform .15s ease;
}
.fip-new-pair-tip::before {
  content: "";
  position: absolute;
  top: -6px;
  left: 18px;
  width: 10px;
  height: 10px;
  background: var(--color-surface-2);
  border-left: 1px solid var(--color-accent);
  border-top: 1px solid var(--color-accent);
  transform: rotate(45deg);
}
.fip-new-pair:hover .fip-new-pair-tip, .fip-new-pair:focus .fip-new-pair-tip { opacity: 1; transform: translateY(0); }
.fip-tip-title { display: block; font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: var(--color-accent-2); }
.fip-tip-line { display: block; margin-top: 6px; font-size: 14px; font-weight: 500; color: var(--color-text); }
.fip-tip-sub { display: block; margin-top: 2px; font-size: 12px; font-weight: 400; color: var(--color-text-muted); }

/* ── Emoji: flags, match-winner medal, champions crown (Noto Color Emoji — Windows has no flag emoji) ── */
.fip-emoji, .fip-theme .fip-emoji { font-family: var(--font-emoji), var(--font-body) !important; }
/* fixed box: no layout jump when the emoji font arrives */
.fip-flag { display: inline-block; min-width: 23px; font-size: 18px; line-height: 1; text-align: center; }
.fip-flag + img.flags { display: none; }   /* the widget's flag image stays only for unknown countries */
.fip-awards { display: inline-flex; gap: 4px; font-size: 20px; line-height: 1; }

/* ── Tournament header (poster thumbnail + name, tier, dates · city) ── */
.fip-header {
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text);
  font-family: var(--font-body);
  text-transform: none;  /* the widget CSS upper-cases the whole body */
  flex-shrink: 0;
}
.fip-header-inner {
  box-sizing: border-box;
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-4);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
}
.fip-header-poster {
  width: 136px;
  aspect-ratio: 4 / 5;
  object-fit: cover;
  object-position: top;
  border-radius: 12px;
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}
.fip-header-info { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.fip-back-link { align-self: flex-start; color: var(--color-accent-2); font-size: 14px; font-weight: 500; text-decoration: none; }
.fip-back-link:hover { color: var(--color-highlight); text-decoration: none; }
.fip-header-title { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.fip-header-name {
  margin: 0;
  color: var(--color-text);
  font-family: var(--font-heading) !important;  /* the widget CSS forces its own font on h1 */
  font-size: clamp(32px, 8vw, 48px);
  font-weight: 700;
  line-height: 1;
  text-transform: uppercase;
}
.fip-header-tier {
  background: var(--color-accent);
  color: var(--color-text);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 999px;
}
.fip-header-sub { font-size: 15px; color: var(--color-text-muted); }

/* ── Date-navigation bar ─────────────────────────────────────── */
.fip-date-nav {
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  font-family: var(--font-body);
  display: flex !important;
  flex-wrap: nowrap !important;
  flex-shrink: 0;
  gap: 10px;
  /* pills line up with the 1200px header column; the bar itself scrolls on narrow screens */
  padding: var(--space-3) max(var(--space-4), calc((100% - 1200px) / 2 + var(--space-4)));
  overflow-x: auto;
}
.fip-day-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  color: var(--color-highlight);
  cursor: pointer;
  padding: var(--space-1) 14px;
  min-width: 64px;
  flex-shrink: 0;
  transition: background .15s ease, border-color .15s ease;
  font-family: inherit;
}
.fip-day-btn:hover:not([disabled]) { border-color: var(--color-accent); }
.fip-day-btn.active { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-text); }
.fip-day-btn[disabled] { opacity: .35; cursor: default; }
.fip-day-weekday { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }
.fip-day-date    { font-family: var(--font-heading); font-size: 20px; font-weight: 700; line-height: 1.1; }

/* ── Match card header: round only (the whole site is women-only) ── */
.round-name b { display: none; }

/* ── Day panel visibility ────────────────────────────────────── */
.fip-day-panel { display: none; }
.fip-day-panel.fip-active { display: block; }

/* ── Empty-day message ───────────────────────────────────────── */
.fip-empty-day {
  text-align: center;
  padding: 60px var(--space-4);
  color: var(--color-text);
  font-family: var(--font-heading);
  font-size: 2rem;
  font-weight: 900;
  font-style: normal;
  text-transform: uppercase;
  letter-spacing: 1px;
}

/* ── Match stats pop-up ──────────────────────────────────────── */
html:has(.fip-stats-dialog[open]) { overflow: hidden; }  /* the page behind does not scroll */
.fip-stats-dialog {
  width: min(560px, calc(100vw - 2 * var(--space-3)));
  max-height: calc(100vh - 2 * var(--space-3));
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  background: var(--color-surface);
  color: var(--color-text);
  font-family: var(--font-body);
  overflow: hidden;
}
.fip-stats-dialog[open] { display: flex; flex-direction: column; }
.fip-stats-dialog::backdrop { background: color-mix(in srgb, var(--color-bg) 80%, transparent); }
.fip-stats-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  padding: var(--space-2) var(--space-2) var(--space-2) 18px;
  background: var(--color-surface-2);
}
.fip-stats-title {
  margin: 0;
  color: var(--color-text);
  font-family: var(--font-heading) !important;  /* the widget CSS forces its own font on headings */
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.fip-stats-close {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--color-highlight);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  transition: background .15s ease;
}
.fip-stats-close:hover { background: var(--color-accent-hover); }
.fip-stats-content { overflow-y: auto; padding: var(--space-3) 18px 18px; }
.fip-stats-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: var(--space-2);
}
.fip-stats-team {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: var(--font-heading);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.15;
  text-transform: uppercase;
}
.fip-stats-team:last-child { text-align: right; }
.fip-stats-result { display: flex; flex-direction: column; align-items: center; }
.fip-stats-score {
  color: var(--color-highlight);
  font-family: var(--font-heading);
  font-size: 26px;
  font-weight: 700;
  line-height: 1.1;
  white-space: nowrap;
}
.fip-stats-time { color: var(--color-text-muted); font-size: 13px; }
.fip-stats-tabs { display: flex; gap: var(--space-1); margin-top: var(--space-3); }
.fip-stats-tab {
  flex: 1;
  padding: 6px var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  color: var(--color-highlight);
  font-family: inherit;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease;
}
.fip-stats-tab:hover { border-color: var(--color-accent); }
.fip-stats-tab.active { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-text); }
.fip-stats-section {
  margin: var(--space-3) 0 0;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-accent-2);
  font-family: var(--font-body) !important;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 1px;
  text-align: center;
  text-transform: uppercase;
}
.fip-stats-row {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) 56px;
  align-items: center;
  padding: 7px 0;
  border-bottom: 1px solid color-mix(in srgb, var(--color-border) 45%, transparent);
}
.fip-stats-label { color: var(--color-text-muted); font-size: 13px; text-align: center; }
.fip-stats-value { font-family: var(--font-heading); font-size: 15px; font-weight: 700; line-height: 1; }
.fip-stats-value:last-child { text-align: right; }
.fip-stats-best { color: var(--color-highlight); }
.fip-stats-value:not(.fip-stats-best) { color: var(--color-text-muted); }

/* ── Sticky footer layout ────────────────────────────────────── */
:root { color-scheme: dark; }  /* the theme is dark-only: native scrollbars and controls follow */
body { display: flex; flex-direction: column; min-height: 100vh; margin: 0; background: var(--color-bg); }
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
  function showDay(day) {
    var btn = document.querySelector('.fip-day-btn[data-day="' + day + '"]:not([disabled])');
    if (!btn) return;
    document.querySelectorAll('.fip-day-btn').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.fip-day-panel').forEach(function (p) { p.classList.remove('fip-active'); });
    btn.classList.add('active');
    var panel = document.getElementById('fip-day-' + day);
    if (panel) panel.classList.add('fip-active');
  }

  /* ── Keep the chosen day across reloads ───────────────────────
     Per tab and per page (sessionStorage). Choosing the page's own default day
     clears the choice, so that tab keeps following the newest day. */
  var DAY_KEY = 'fip-day:' + location.pathname;
  function stored(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }
  function store(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (e) { /* storage unavailable → default day after each refresh */ }
  }
  var defaultBtn = document.querySelector('.fip-day-btn.active');
  var defaultDay = defaultBtn ? defaultBtn.dataset.day : null;

  document.querySelectorAll('.fip-day-btn:not([disabled])').forEach(function (btn) {
    btn.addEventListener('click', function () {
      showDay(btn.dataset.day);
      store(DAY_KEY, btn.dataset.day === defaultDay ? null : btn.dataset.day);
    });
  });

  var savedDay = stored(DAY_KEY);
  if (savedDay) showDay(savedDay);

  /* ── Live refresh without reloading ───────────────────────────
     Fetches this page again and swaps only the day panels whose HTML changed, so
     the chosen day and the scroll position stay where the viewer left them.
     The interval follows the page's <meta name="fip-refresh"> (15 s live, else 60 s). */
  var refreshMeta = document.querySelector('meta[name="fip-refresh"]');
  var refreshSeconds = refreshMeta ? parseInt(refreshMeta.content, 10) : 0;
  var ownScript = document.currentScript ? document.currentScript.textContent : null;
  var panelHtml = {};  // as served — the live DOM differs (venue clock)
  document.querySelectorAll('.fip-day-panel').forEach(function (p) { panelHtml[p.id] = p.innerHTML; });

  function applyRefresh(doc) {
    var newScript = doc.querySelector('body > script');
    if (ownScript !== null && newScript && newScript.textContent !== ownScript) {
      location.reload();  // the page's own code changed
      return;
    }
    doc.querySelectorAll('.fip-day-panel').forEach(function (fresh) {
      var panel = document.getElementById(fresh.id);
      if (!panel || panelHtml[fresh.id] === fresh.innerHTML) return;
      panelHtml[fresh.id] = fresh.innerHTML;
      panel.innerHTML = fresh.innerHTML;
    });
    // a new day got its schedule: follow it unless the viewer chose a day
    var freshDefault = doc.querySelector('.fip-day-btn.active');
    if (freshDefault && freshDefault.dataset.day !== defaultDay) {
      defaultDay = freshDefault.dataset.day;
      if (!stored(DAY_KEY)) showDay(defaultDay);
    }
    var meta = doc.querySelector('meta[name="fip-refresh"]');
    if (meta) refreshSeconds = parseInt(meta.content, 10) || refreshSeconds;
    if (TIMEZONE) updateClocks();
    if (statsDialog && statsDialog.open) drawStats();  // live match: new numbers
  }
  function refresh() {
    fetch(location.href, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { applyRefresh(new DOMParser().parseFromString(html, 'text/html')); })
      .catch(function () {
        if (location.protocol === 'file:') location.reload();  // fetch is blocked there
      })
      .then(function () { setTimeout(refresh, refreshSeconds * 1000); });
  }
  if (refreshSeconds > 0) setTimeout(refresh, refreshSeconds * 1000);

  /* ── Match stats pop-up ───────────────────────────────────────
     A card's "Match stats" button names its match (data-match); the numbers are in the
     day panel's <script class="fip-stats-data"> block: {matchId: {score, time, teams, periods}}.
     One tab per period (Match / Set 1 / …), one row per figure: value · label · value. */
  var statsDialog = document.getElementById('fip-stats-dialog');
  var statsContent = statsDialog ? statsDialog.querySelector('.fip-stats-content') : null;
  var statsMatch = null;   // match shown in the pop-up
  var statsPeriod = 0;     // chosen tab
  var statsDrawn = null;   // what is on screen, to skip redrawing unchanged numbers

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function statsOf(matchId) {
    var btn = document.querySelector('.fip-stats-btn[data-match="' + matchId + '"]');
    var panel = btn ? btn.closest('.fip-day-panel') : null;
    var data = panel ? panel.querySelector('script.fip-stats-data') : null;
    try { return data ? JSON.parse(data.textContent)[matchId] : null; } catch (e) { return null; }
  }
  function teamBlock(names) {
    var team = el('div', 'fip-stats-team');
    (names || []).forEach(function (name) { team.appendChild(el('span', null, name)); });
    return team;
  }
  function drawStats() {
    var stats = statsOf(statsMatch);
    if (!stats) return;  // match no longer on the page → keep what is shown
    if (statsPeriod >= stats.periods.length) statsPeriod = 0;
    var key = statsPeriod + JSON.stringify(stats);
    if (key === statsDrawn) return;
    statsDrawn = key;
    var hadFocus = statsContent.contains(document.activeElement);
    statsContent.textContent = '';

    var head = el('div', 'fip-stats-head');
    var result = el('div', 'fip-stats-result');
    result.appendChild(el('span', 'fip-stats-score', stats.score));
    result.appendChild(el('span', 'fip-stats-time', stats.time));
    head.appendChild(teamBlock(stats.teams[0]));
    head.appendChild(result);
    head.appendChild(teamBlock(stats.teams[1]));
    statsContent.appendChild(head);

    var tabs = el('div', 'fip-stats-tabs');
    tabs.setAttribute('role', 'tablist');
    stats.periods.forEach(function (period, i) {
      var tab = el('button', 'fip-stats-tab' + (i === statsPeriod ? ' active' : ''), period.name);
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', i === statsPeriod ? 'true' : 'false');
      tab.addEventListener('click', function () { statsPeriod = i; drawStats(); });
      tabs.appendChild(tab);
    });
    statsContent.appendChild(tabs);

    stats.periods[statsPeriod].sections.forEach(function (section) {
      if (section.title) statsContent.appendChild(el('h3', 'fip-stats-section', section.title));
      section.rows.forEach(function (row) {
        var a = parseFloat(row[1]), b = parseFloat(row[2]);
        var line = el('div', 'fip-stats-row');
        line.appendChild(el('span', 'fip-stats-value' + (a > b ? ' fip-stats-best' : ''), row[1]));
        line.appendChild(el('span', 'fip-stats-label', row[0]));
        line.appendChild(el('span', 'fip-stats-value' + (b > a ? ' fip-stats-best' : ''), row[2]));
        statsContent.appendChild(line);
      });
    });
    if (hadFocus) tabs.children[statsPeriod].focus();  // the clicked tab was redrawn
  }
  if (statsDialog && statsDialog.showModal) {
    document.getElementById('fip-panels-wrapper').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.fip-stats-btn') : null;
      if (!btn) return;
      statsMatch = btn.dataset.match;
      statsPeriod = 0;
      statsDrawn = null;
      drawStats();
      if (statsDrawn !== null) statsDialog.showModal();
    });
    statsDialog.querySelector('.fip-stats-close').addEventListener('click', function () { statsDialog.close(); });
    // the dialog has no padding, so a click on the element itself is a click on the backdrop
    statsDialog.addEventListener('click', function (e) { if (e.target === statsDialog) statsDialog.close(); });
  }

  /* ── Date nav: keep the active day in view when the bar scrolls (mobile) ── */
  var activeBtn = document.querySelector('.fip-day-btn.active');
  var dateNav = document.querySelector('.fip-date-nav');
  if (activeBtn && dateNav) {
    dateNav.scrollLeft = activeBtn.offsetLeft - (dateNav.clientWidth - activeBtn.offsetWidth) / 2;
  }

  /* ── Live venue clock (tournament's own time zone, DST handled by the browser) ──
     Court header only: "9:14 AM · CEST (UTC+2)". The abbreviation comes from Intl;
     zones Intl has no abbreviation for show the offset alone ("9:14 AM · UTC−3"). */
  var TIMEZONE = __FIP_TIMEZONE__;
  function zonePart(now, locale, style) {
    var parts = new Intl.DateTimeFormat(locale, { timeZone: TIMEZONE, timeZoneName: style }).formatToParts(now);
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type === 'timeZoneName') return parts[i].value;
    }
    return '';
  }
  function venueTime() {
    var now = new Date();
    var time = new Intl.DateTimeFormat('en-US', {
      timeZone: TIMEZONE, hour: 'numeric', minute: '2-digit', hour12: true
    }).format(now);
    var offset = 'UTC' + (zonePart(now, 'en-US', 'shortOffset').slice(3) || '+0').replace('-', '−');
    var abbr = '';
    ['en-US', 'en-GB'].forEach(function (locale) {
      var name = zonePart(now, locale, 'short');
      if (!abbr && !/^(GMT|UTC)[+−-]/.test(name)) abbr = name;
    });
    return time + ' · ' + (abbr ? abbr + ' (' + offset + ')' : offset);
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


_LINK_ICON = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"></path>'
    '<path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"></path>'
    "</svg>"
)
# One tooltip line: "Previously with M. Calvo (Buenos Aires P1)" or "A. Sanchez: previously with …"
_PAIR_LINE_RE = re.compile(r"^(?:(?P<who>.+?): )?previously with (?P<partner>.+) \((?P<where>[^()]+)\)$", re.I)


def _new_pair_button(tooltip: str) -> str:
    """HTML of the NEW PAIR pill: a real <button> (keyboard focus) holding the details card."""
    tip = ['<span class="fip-tip-title">New partnership</span>']
    for line in tooltip.splitlines():
        m = _PAIR_LINE_RE.match(line.strip())
        if not m:
            tip.append(f'<span class="fip-tip-line">{escape(line)}</span>')
            continue
        who = f"{m['who']}: previously" if m["who"] else "Previously"
        tip.append(f'<span class="fip-tip-line">{escape(who)} with {escape(m["partner"])}</span>')
        tip.append(f'<span class="fip-tip-sub">at {escape(m["where"])}</span>')
    label = "New pair: " + "; ".join(tooltip.splitlines())
    return (
        f'<button type="button" class="fip-new-pair" aria-label="{escape(label)}">'
        f"{_LINK_ICON}New pair"
        f'<span class="fip-new-pair-tip">{"".join(tip)}</span>'
        "</button>"
    )


def inject_new_pair_badges(body_html: str, pair_info) -> str:
    """Add a ``NEW PAIR`` pill under the names of every team that is a new partnership.

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
        badge = BeautifulSoup(_new_pair_button(tooltip), "html.parser")
        # Under the two names (falls back to the first name's line)
        holder = team_td.find("div", class_="player-names") or name_divs[0]
        holder.append(badge)

    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    return body_el.decode_contents() if body_el else str(soup)


# Widget flag images are named by 3-letter sports code (…/flags/ESP.jpg) → ISO 3166 alpha-2
_COUNTRY_ISO2 = {
    "AND": "AD", "ARG": "AR", "AUS": "AU", "AUT": "AT", "BEL": "BE", "BOL": "BO", "BRA": "BR", "BRN": "BH",
    "BUL": "BG", "CAN": "CA", "CHI": "CL", "CHN": "CN", "COL": "CO", "CRC": "CR", "CRO": "HR", "CUB": "CU",
    "CZE": "CZ", "DEN": "DK", "DOM": "DO", "ECU": "EC", "EGY": "EG", "ESP": "ES", "EST": "EE", "FIN": "FI",
    "FRA": "FR", "GBR": "GB", "GER": "DE", "GRE": "GR", "GUA": "GT", "HUN": "HU", "IND": "IN", "IRI": "IR",
    "IRL": "IE", "ISR": "IL", "ITA": "IT", "JPN": "JP", "KOR": "KR", "KSA": "SA", "KUW": "KW", "LAT": "LV",
    "LTU": "LT", "LUX": "LU", "MAR": "MA", "MEX": "MX", "MON": "MC", "NED": "NL", "NOR": "NO", "NZL": "NZ",
    "PAN": "PA", "PAR": "PY", "PER": "PE", "PHI": "PH", "POL": "PL", "POR": "PT", "PUR": "PR", "QAT": "QA",
    "ROU": "RO", "RSA": "ZA", "RUS": "RU", "SEN": "SN", "SLO": "SI", "SRB": "RS", "SUI": "CH", "SVK": "SK",
    "SWE": "SE", "THA": "TH", "TUN": "TN", "TUR": "TR", "UAE": "AE", "UKR": "UA", "URU": "UY", "USA": "US",
    "VEN": "VE",
}


def _flag_emoji(code: str) -> str | None:
    """'ESP' → '🇪🇸' (None for a country code that is not in the table)."""
    iso2 = _COUNTRY_ISO2.get(code.upper())
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2) if iso2 else None


def inject_emoji(body_html: str) -> str:
    """Add the emoji decorations to widget body HTML.

    - a flag emoji before each player's flag image (the image is then hidden by CSS;
      an unknown country code keeps the image);
    - 🏅 next to the winning pair of every completed match;
    - 👑 next to the winning pair of the tournament's final.
    """
    soup = BeautifulSoup(body_html, "html.parser")

    def emoji(char: str, label: str, cls: str | None = None):
        span = soup.new_tag("span", role="img", title=label)
        span["aria-label"] = label
        if cls:
            span["class"] = cls
        span.string = char
        return span

    for img in soup.find_all("img", class_="flags"):
        code = Path(urlparse(img.get("src", "")).path).stem.upper()
        flag = _flag_emoji(code)
        if flag:
            img.insert_before(emoji(flag, code, "fip-emoji fip-flag"))

    for table in soup.find_all("table", class_="w-100"):
        if not table.find("tr", class_="scorebox-header-completed"):
            continue
        round_el = table.find("div", class_="round-name")
        round_text = round_el.get_text(" ", strip=True) if round_el else ""
        category = round_el.find("b") if round_el else None
        if category:
            round_text = round_text.replace(category.get_text(strip=True), "", 1)
        is_final = round_text.strip().lower() == "final"

        for team_td in table.find_all("td", class_="team"):
            side = team_td.find("div", class_="mr-2")
            if not side or not team_td.find("div", class_="winner"):
                continue
            awards = soup.new_tag("span")
            awards["class"] = "fip-emoji fip-awards"
            if is_final:
                awards.append(emoji("👑", "Tournament champions"))
            awards.append(emoji("🏅", "Match winners"))
            side.insert(0, awards)

    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    return body_el.decode_contents() if body_el else str(soup)


def inject_match_stats(body_html: str, stats_for) -> str:
    """Turn the widget's dead "MATCH STATS" links into buttons that open the stats pop-up.

    Args:
        body_html: inner HTML of the widget ``<body>``, already gender-filtered.
        stats_for: ``callable(match_id) -> stats dict | None`` (see ``scrape_stats.parse_stats_html``).

    A link whose match has no stats is removed. The stats of the day are appended as one
    ``<script type="application/json" class="fip-stats-data">`` block (``{match_id: stats}``),
    read by the page's script — inside the day panel, so the in-place refresh carries it along.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    day_stats = {}
    for link in soup.find_all("a", class_="open"):
        match_id = link.get("data-id")
        stats = stats_for(match_id) if match_id else None
        if not stats:
            link.decompose()
            continue
        day_stats[match_id] = stats
        button = soup.new_tag("button", type="button")
        button["class"] = "fip-stats-btn"
        button["data-match"] = match_id
        button.string = "Match stats"
        link.replace_with(button)

    # Return only the inner content — avoid html.parser's <html><body> wrapper
    body_el = soup.find("body")
    html = body_el.decode_contents() if body_el else str(soup)
    if day_stats:
        data = json.dumps(day_stats, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        html += f'\n<script type="application/json" class="fip-stats-data">{data}</script>'
    return html


# The one pop-up of the page; its content is drawn by the page's script from the day's stats data
_STATS_DIALOG = (
    '<dialog id="fip-stats-dialog" class="fip-stats-dialog" aria-labelledby="fip-stats-title">\n'
    '  <div class="fip-stats-bar">\n'
    '    <h2 id="fip-stats-title" class="fip-stats-title">Match stats</h2>\n'
    '    <button type="button" class="fip-stats-close" aria-label="Close">×</button>\n'
    "  </div>\n"
    '  <div class="fip-stats-content"></div>\n'
    "</dialog>\n"
)


def _format_location(location: str | None) -> str | None:
    """'Buenos aires - Argentina' → 'Buenos Aires, Argentina'."""
    if not location:
        return None
    return ", ".join(part.strip().title() for part in location.split(" - "))


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
    header: dict | None = None,
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
        assets_dir:      Where theme.css and overrides.css live (default: ``assets/`` next to the output).
        back_href:       If set, a small "← All tournaments" link to this URL is added to the header.
        timezone_name:   IANA zone of the venue (e.g. ``"Europe/Amsterdam"``) for the live
                         "Local Time" clock. None leaves the widget's own text untouched.
        header:          ``{name, year, tier, dates, location, image}`` for the page header
                         (all optional; without it the header shows *tournament_name* only).
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Download DINPro fonts locally so they load without CORS issues
    fonts_dir = Path(fonts_dir) if fonts_dir else out.parent / "fonts"
    fonts_ok = _ensure_fonts(fonts_dir)
    fonts_href = os.path.relpath(fonts_dir.resolve(), out.parent.resolve()).replace(os.sep, "/")
    font_face_css = _font_face_css(fonts_ok, fonts_href if fonts_href.startswith(".") else f"./{fonts_href}")

    # Days that still hold match tables after the gender filter
    with_matches = {
        day_num for day_num, body in bodies_by_day.items()
        if body and BeautifulSoup(body, "html.parser").find("table", class_="w-100")
    }
    # Default active day: the last one with women's matches (a men's final can be played a
    # day later), else the last one with a schedule
    active_day = max(with_matches or [day_num for day_num, body in bodies_by_day.items() if body] or [1])

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
        elif day_num not in with_matches:
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
        # read by the page's script, which refreshes the panels in place;
        # a full reload is only the no-JavaScript fallback
        f'<meta name="fip-refresh" content="{refresh_interval}">\n',
        f'<noscript><meta http-equiv="refresh" content="{refresh_interval}"></noscript>\n',
        f"<title>Order of Play — {escape(tournament_name)}</title>\n",
        f"{link_tags}\n",
        # after the widget's stylesheets, so overrides.css wins
        f"{_theme_link_tags(out, assets_dir, (_THEME_FILE, _OVERRIDES_FILE))}\n",
        f"<style>\n{font_face_css}{_CSS}</style>\n",
        "</head>\n",
        "<body>\n",
        '<header class="fip-header"><div class="fip-header-inner">\n',
    ]
    header = header or {}
    heading = escape(short_name(header["name"]) if header.get("name") else tournament_name)
    if header.get("image"):
        # A poster that fails to load removes itself; the header then starts with the text
        parts.append(
            f'  <img class="fip-header-poster" src="{escape(header["image"])}" alt="{heading} poster" '
            'onerror="this.remove()">\n'
        )
    parts.append('  <div class="fip-header-info">\n')
    if back_href:
        parts.append(f'    <a class="fip-back-link" href="{escape(back_href)}">← All tournaments</a>\n')
    tier = f'<span class="fip-header-tier">{escape(header["tier"])}</span>' if header.get("tier") else ""
    parts.append(f'    <div class="fip-header-title"><h1 class="fip-header-name">{heading}</h1>{tier}</div>\n')
    subtitle = " · ".join(filter(None, [header.get("dates"), _format_location(header.get("location"))]))
    if subtitle:
        parts.append(f'    <div class="fip-header-sub">{escape(subtitle)}</div>\n')
    parts.append("  </div>\n</div></header>\n")
    parts.append('<nav class="fip-date-nav" aria-label="Tournament days">\n')
    for btn in nav_btns:
        parts.append(f"  {btn}\n")
    parts.append("</nav>\n")
    parts.append('<div id="fip-panels-wrapper" class="fip-theme">\n')
    for panel in panels:
        parts.append(panel + "\n")
    parts.append('</div>\n')
    parts.append(_STATS_DIALOG)
    parts.extend([
        f'<footer class="fip-footer">Made by ice<span class="fip-emoji">🧊</span> &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        f"<script>\n{_JS.replace('__FIP_TIMEZONE__', json.dumps(timezone_name))}</script>\n",
        "</body>\n",
        "</html>",
    ])

    html = "".join(parts)

    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")



# ── Landing page (list of tournaments) ────────────────────────────────────────

_LANDING_CSS = """\
:root { color-scheme: dark; }  /* the theme is dark-only: native scrollbars and controls follow */
body { margin: 0; font-family: var(--font-body); color: var(--color-text); background: var(--color-bg); display: flex; flex-direction: column; min-height: 100vh; }
.fip-landing-header { border-bottom: 1px solid var(--color-border); }
.fip-landing-header-inner, .fip-landing-main { box-sizing: border-box; width: 100%; max-width: 1200px; margin: 0 auto; }
.fip-landing-header-inner { padding: 20px var(--space-4); display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; justify-content: space-between; }
.fip-brand-wrap { display: flex; align-items: center; gap: var(--space-3); }
.fip-brand-logo { display: block; border-radius: 4px; transition: opacity .15s ease; }
.fip-brand-logo:hover { opacity: .8; }
.fip-brand-logo:focus-visible { outline: 2px solid var(--color-accent-2); outline-offset: 4px; }
.fip-brand-logo img { display: block; height: 40px; width: auto; }
.fip-brand-divider { width: 1px; height: 28px; background: var(--color-border); }
.fip-brand { font-family: var(--font-heading); font-weight: 700; font-size: 26px; letter-spacing: .5px; text-transform: uppercase; }
.fip-brand span { color: var(--color-accent-2); }
a.fip-brand { color: var(--color-text); text-decoration: none; border-radius: 4px; transition: opacity .15s ease; }
a.fip-brand:hover { opacity: .8; }
a.fip-brand:focus-visible { outline: 2px solid var(--color-accent-2); outline-offset: 4px; }
.fip-tagline { font-size: 14px; color: var(--color-text-muted); }
@media (max-width: 480px) {
  .fip-brand-wrap { gap: var(--space-2); }
  .fip-brand-logo img { height: 32px; }
  .fip-brand { font-size: 20px; }
}
.fip-landing-main { flex: 1; padding: calc(var(--space-4) * 2) var(--space-4) calc(var(--space-4) * 2 + var(--space-3)); }
.fip-landing-title { margin: var(--space-1) 0; font-family: var(--font-heading); font-weight: 700; font-size: 56px; line-height: 1; text-transform: uppercase; }
.fip-landing-intro { margin: 0 0 calc(var(--space-4) + var(--space-3)); font-size: 17px; color: var(--color-text-muted); }

/* ── Season + month filter ───────────────────────────────────── */
.fip-landing-main [hidden] { display: none; }
.fip-filter { display: flex; align-items: center; gap: var(--space-4); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; padding: 10px var(--space-3); margin: 0 0 calc(var(--space-4) + var(--space-3)); }
.fip-year { display: flex; align-items: center; gap: var(--space-2); flex: none; padding-right: var(--space-4); border-right: 1px solid var(--color-border); color: var(--color-accent-2); }
.fip-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.fip-select-wrap { position: relative; display: flex; align-items: center; color: var(--color-text); }
.fip-select { appearance: none; -webkit-appearance: none; background: transparent; color: var(--color-text); border: 0; border-bottom: 1px solid var(--color-accent-2); border-radius: 0; padding: 6px 26px 6px 2px; min-height: 44px; font-family: var(--font-heading); font-weight: 700; font-size: 20px; letter-spacing: .5px; cursor: pointer; }
.fip-select option { background: var(--color-surface); color: var(--color-text); }
.fip-select:focus-visible { outline: 2px solid var(--color-highlight); outline-offset: 2px; }
.fip-select-chev { position: absolute; right: 4px; pointer-events: none; }
.fip-months { display: flex; gap: 2px; overflow-x: auto; flex: 1; min-width: 0; scrollbar-width: thin; }
.fip-mtab { flex: none; min-height: 44px; padding: 0 8px;  /* 12 months + TBC fit the 1200px layout without scrolling */ border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--color-text); font-family: var(--font-heading); font-weight: 700; font-size: 18px; letter-spacing: .5px; text-transform: uppercase; cursor: pointer; transition: background .15s ease, color .15s ease; }
.fip-mtab:hover:not(:disabled) { background: var(--color-surface-2); }
.fip-mtab:focus-visible { outline: none; border: 1px dashed var(--color-highlight); }
.fip-mtab:disabled { color: var(--color-text-muted); opacity: .55; cursor: default; }
.fip-mtab.is-selected, .fip-mtab.is-selected:hover { background: var(--color-highlight); color: var(--color-bg); }
.fip-empty { padding: 48px var(--space-4); text-align: center; border: 1px dashed var(--color-border); border-radius: var(--radius-card); color: var(--color-text-muted); font-size: 16px; }
@media (max-width: 720px) {
  .fip-filter { flex-wrap: wrap; gap: var(--space-2); }
  .fip-year { border-right: 0; padding-right: 0; }
  .fip-months { flex-basis: 100%; }
}

/* ── Month groups ────────────────────────────────────────────── */
.fip-month + .fip-month { margin-top: var(--space-4); }
.fip-month-year { color: var(--color-text-muted); }
.fip-filtered .fip-month-year { display: none; }  /* the season is in the page title once the filter runs */
.fip-month-title { margin: 0 0 var(--space-3); padding-bottom: var(--space-1); border-bottom: 1px solid var(--color-border); font-family: var(--font-heading); font-weight: 700; font-size: 28px; line-height: 1; letter-spacing: .5px; text-transform: uppercase; color: var(--color-text); }

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
a.fip-card:hover, a.fip-card:focus-visible { transform: translateY(-6px); box-shadow: var(--shadow-card-hover); border-color: var(--color-accent); outline: none; color: var(--color-text); }
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
.fip-emoji { font-family: var(--font-emoji), var(--font-body); }
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

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_NO_DATE_HEADING = "Date TBC"   # tournaments without a start date (postponed), shown last
_NO_DATE_KEY = "tbc"            # their month in data-month, the tab and the URL hash

_CALENDAR_ICON = (
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="4.5" width="18" height="16.5" rx="2.5"></rect>'
    '<line x1="3" y1="9.5" x2="21" y2="9.5"></line>'
    '<line x1="8" y1="2.5" x2="8" y2="6.5"></line>'
    '<line x1="16" y1="2.5" x2="16" y2="6.5"></line>'
    '<circle cx="8" cy="13.5" r=".6" fill="currentColor"></circle>'
    '<circle cx="12" cy="13.5" r=".6" fill="currentColor"></circle>'
    '<circle cx="16" cy="13.5" r=".6" fill="currentColor"></circle>'
    '<circle cx="8" cy="17" r=".6" fill="currentColor"></circle>'
    '<circle cx="12" cy="17" r=".6" fill="currentColor"></circle>'
    "</svg>"
)
_CHEVRON_ICON = (
    '<svg class="fip-select-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="6 9 12 15 18 9"></polyline></svg>'
)

# Season + month filter. Every season's month sections are in the page; the script shows one
# (season, month) at a time and keeps the choice in the URL hash (#2025-03, #2026-tbc, #live).
# Without JavaScript the filter bar stays hidden and every section is visible.
_LANDING_JS = """\
(function () {
  var main = document.querySelector('.fip-landing-main');
  var nav = document.getElementById('fip-filter');
  var select = document.getElementById('fip-year-select');
  if (!main || !nav || !select) return;
  var tabs = [].slice.call(nav.querySelectorAll('.fip-mtab'));
  var sections = [].slice.call(main.querySelectorAll('.fip-month'));
  var title = main.querySelector('.fip-landing-title');
  var empty = main.querySelector('.fip-empty');
  var logo = document.querySelector('.fip-brand-logo');
  var years = [].map.call(select.options, function (o) { return o.value; });

  /* Months of a season that have tournaments, in page order ('1'…'12', then 'tbc') */
  function monthsOf(year) {
    return sections.filter(function (s) { return s.dataset.year === year; })
                   .map(function (s) { return s.dataset.month; });
  }

  function defaultYear() {
    var cur = String(new Date().getFullYear());
    return years.indexOf(cur) !== -1 ? cur : years[years.length - 1];
  }

  /* This month when it has tournaments, else the season's first month that does */
  function defaultMonth(year) {
    var months = monthsOf(year);
    var now = new Date();
    var cur = String(now.getMonth() + 1);
    if (String(now.getFullYear()) === year && months.indexOf(cur) !== -1) return cur;
    return months.length ? months[0] : null;
  }

  /* Keep the selected tab in view when the tabs scroll sideways (phones) */
  function centerTab() {
    var tab = nav.querySelector('.fip-mtab.is-selected');
    if (!tab) return;
    var box = tab.parentNode;
    box.scrollLeft = tab.offsetLeft - box.offsetLeft - (box.clientWidth - tab.offsetWidth) / 2;
  }

  function show(year, month) {
    if (years.indexOf(year) === -1) year = defaultYear();
    var months = monthsOf(year);
    if (months.indexOf(month) === -1) month = defaultMonth(year);

    select.value = year;
    tabs.forEach(function (tab) {
      var m = tab.dataset.month;
      var selected = m === month;
      tab.disabled = months.indexOf(m) === -1;
      tab.hidden = m === 'tbc' && tab.disabled;
      tab.classList.toggle('is-selected', selected);
      tab.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    centerTab();
    sections.forEach(function (s) {
      s.hidden = !(s.dataset.year === year && s.dataset.month === month);
    });
    if (empty) empty.hidden = month !== null;
    if (title) title.textContent = year + ' Season';
    document.title = document.title.replace(/20[0-9]{2}/, year);
    if (logo) logo.href = logo.href.replace(/events-year=[0-9]+/, 'events-year=' + year);

    var hash = '#' + year + (month ? '-' + (month.length < 2 ? '0' + month : month) : '');
    try { history.replaceState(null, '', hash); } catch (e) {}
  }

  /* #live is the month of the tournament being played (set by the brand link) */
  function fromHash() {
    var hash = location.hash.slice(1);
    if (hash === 'live') hash = nav.dataset.live || '';
    var m = /^(20[0-9]{2})(?:-([0-9]{1,2}|tbc))?$/.exec(hash);
    if (!m) return show(defaultYear(), null);
    show(m[1], m[2] && m[2] !== 'tbc' ? String(parseInt(m[2], 10)) : m[2] || null);
  }

  select.addEventListener('change', function () { show(select.value, null); });
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () { show(select.value, tab.dataset.month); });
  });
  window.addEventListener('hashchange', fromHash);

  nav.hidden = false;
  main.classList.add('fip-filtered');
  fromHash();
  /* The heading font loads late and changes the tab widths */
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(centerTab);
})();
"""


def generate_landing_html(rows: list[dict], output_path: str) -> None:
    """Write the landing page: one card per tournament, grouped by season and by the month it starts in.

    Each row: ``{name, year, tier, dates, start, status, href, image}`` — ``year`` is the
    season; ``href`` is None when the tournament has no page (upcoming, postponed, or no
    data); ``image`` is the poster URL, or None for the court-drawing fallback; ``start``
    is the start date, or None (those cards go into the season's "Date TBC" group).
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # (season, month) → cards; a 30 Nov – 6 Dec tournament belongs to November
    dated = sorted((r for r in rows if r.get("start")), key=lambda r: r["start"])
    undated = [r for r in rows if not r.get("start")]
    months: dict[tuple[int, int | None], list[str]] = {}
    live_key = None   # month of the first ongoing tournament — the brand link selects it
    for row in dated + undated:
        start = row.get("start")
        year = row["year"]
        cards = months.setdefault((year, start.month if start else None), [])
        if start and live_key is None and row["status"] == "Ongoing":
            live_key = (year, start.month)
        name = escape(short_name(row["name"]))   # series and season are in the page heading
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

    sections: list[str] = []
    for year, month in sorted(months, key=lambda k: (k[0], k[1] or 13)):
        heading = _MONTH_NAMES[month - 1] if month else _NO_DATE_HEADING
        sections.append(
            f'<section class="fip-month" data-year="{year}" data-month="{month or _NO_DATE_KEY}">\n'
            f'<h2 class="fip-month-title">{heading} <span class="fip-month-year">{year}</span></h2>\n'
            '<div class="fip-card-grid">\n' + "\n".join(months[(year, month)]) + "\n</div>\n"
            "</section>"
        )

    # The season shown first (and the only one named without JavaScript): this year's, else the newest
    this_year = date.today().year
    years = sorted({year for year, _ in months}) or [this_year]
    shown_year = this_year if this_year in years else years[-1]
    year_options = "".join(
        f'<option value="{y}"{" selected" if y == shown_year else ""}>{y}</option>' for y in years
    )
    month_tabs = "".join(
        f'<button type="button" class="fip-mtab" data-month="{key}" aria-pressed="false">{label}</button>'
        for key, label in [*enumerate(_MONTH_NAMES, start=1), (_NO_DATE_KEY, "TBC")]
    )
    live_attr = f' data-live="{live_key[0]}-{live_key[1]:02d}"' if live_key else ""

    logo_src = os.path.relpath(_ASSETS_SOURCE / _LOGO_FILE, out.parent.resolve()).replace(os.sep, "/")
    calendar_href = f"{_CALENDAR_URL}?events-year={shown_year}"
    # The brand reloads the page; #live selects the month of the ongoing tournament
    # (none → this season and this month)
    brand_attrs = '''href="#live" onclick="location.hash='live';scrollTo(0,0);location.reload();return false"'''
    title = f"Premier Padel {shown_year} — Women"

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
        '<div class="fip-brand-wrap">'
        f'<a class="fip-brand-logo" href="{escape(calendar_href)}" target="_blank" rel="noopener" '
        'title="Premier Padel calendar on padelfip.com">'
        f'<img src="{escape(logo_src)}" alt="Premier Padel"></a>'
        '<span class="fip-brand-divider" aria-hidden="true"></span>'
        f'<a class="fip-brand" {brand_attrs}>Premier Padel <span>Women</span></a>'
        "</div>\n",
        '<div class="fip-tagline">FIP rankings next to every player</div>\n',
        "</div></header>\n",
        '<main class="fip-landing-main">\n',
        f'<h1 class="fip-landing-title">{shown_year} Season</h1>\n',
        '<p class="fip-landing-intro">Pick a tournament to see the women\'s order of play, scores and FIP ranks.</p>\n',
        f'<nav class="fip-filter" id="fip-filter" aria-label="Season and month"{live_attr} hidden>\n'
        f'<div class="fip-year">{_CALENDAR_ICON}'
        '<label class="fip-sr" for="fip-year-select">Season</label>'
        f'<div class="fip-select-wrap"><select id="fip-year-select" class="fip-select">{year_options}</select>'
        f"{_CHEVRON_ICON}</div></div>\n"
        f'<div class="fip-months">{month_tabs}</div>\n'
        "</nav>\n",
        '<div class="fip-empty" hidden>No tournaments for this season yet.</div>\n',
        "\n".join(sections) + "\n",
        f"<script>\n{_LANDING_JS}</script>\n",
        "</main>\n",
        '<footer class="fip-footer">Made by ice<span class="fip-emoji">🧊</span> &nbsp;·&nbsp; Rankings: padelfip.com</footer>\n',
        "</body>\n",
        "</html>",
    ])
    out.write_text(html, encoding="utf-8")
    print(f"[html] Saved -> {out.resolve()}")
