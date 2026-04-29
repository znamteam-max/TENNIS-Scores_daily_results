from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler

from gha_worker import run_once


CRON_SECRET = os.getenv("CRON_SECRET", "").strip()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if CRON_SECRET:
            expected = f"Bearer {CRON_SECRET}"
            if self.headers.get("authorization", "") != expected:
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"unauthorized"}')
                return

        try:
            asyncio.run(run_once())
            payload = {"ok": True}
        except Exception as exc:
            print(f"[ERR] cron poll failed: {exc}")
            payload = {"ok": False, "error": str(exc)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
