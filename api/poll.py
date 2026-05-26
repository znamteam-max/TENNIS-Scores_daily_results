from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from gha_worker import run_once


CRON_SECRET = os.getenv("CRON_SECRET", "").strip()


class handler(BaseHTTPRequestHandler):
    def _is_authorized(self):
        if not CRON_SECRET:
            return True

        expected = f"Bearer {CRON_SECRET}"
        if self.headers.get("authorization", "") == expected:
            return True

        query = parse_qs(urlparse(self.path).query)
        return (query.get("secret") or [""])[0] == CRON_SECRET

    def do_GET(self):
        if not self._is_authorized():
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"unauthorized"}')
            return

        try:
            result = asyncio.run(run_once()) or {}
            payload = {"ok": True, **result}
        except Exception as exc:
            print(f"[ERR] cron poll failed: {exc}")
            payload = {"ok": False, "error": str(exc)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
