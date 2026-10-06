"""Vercel function: GET /api/live?t=<tournament slug> — see live_feed.py."""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the project's modules

from live_feed import answer   # noqa: E402


class handler(BaseHTTPRequestHandler):   # noqa: N801 — the name Vercel looks for
    def do_GET(self):   # noqa: N802
        slug = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        status, body, seconds = answer(slug)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Browsers always ask; Vercel's CDN answers from its cache until the next change is due
        self.send_header("Cache-Control", f"public, max-age=0, s-maxage={seconds}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
