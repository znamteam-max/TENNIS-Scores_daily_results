from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
ALLOWED_HOSTS = {
    "tennis-scores-daily-results.vercel.app",
    "tennis-scores-daily-results-1.vercel.app",
    "tennis-scores-daily-results-main.vercel.app",
}


class handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _is_authorized(self) -> bool:
        if not CRON_SECRET:
            return False
        if self.headers.get("authorization", "") == f"Bearer {CRON_SECRET}":
            return True
        query = parse_qs(urlparse(self.path).query)
        return (query.get("secret") or [""])[0] == CRON_SECRET

    def do_GET(self) -> None:
        if not self._is_authorized():
            self._send_json({"ok": False, "error": "unauthorized"}, 401)
            return
        if not BOT_TOKEN:
            self._send_json({"ok": False, "error": "TELEGRAM_BOT_TOKEN is not set"}, 500)
            return

        host = self.headers.get("x-forwarded-host") or self.headers.get("host") or ""
        if not host:
            self._send_json({"ok": False, "error": "host header is missing"}, 400)
            return

        query = parse_qs(urlparse(self.path).query)
        target_host = (query.get("host") or [host])[0].strip().lower()
        if target_host not in ALLOWED_HOSTS:
            self._send_json({"ok": False, "error": "target host is not allowed"}, 400)
            return

        target = f"https://{target_host}/api/webhook"
        drop_pending = (query.get("drop_pending_updates") or ["0"])[0].lower() in {"1", "true", "yes", "on"}
        payload = {
            "url": target,
            "allowed_updates": json.dumps(["message", "callback_query"]),
            "drop_pending_updates": "true" if drop_pending else "false",
        }
        if WEBHOOK_SECRET:
            payload["secret_token"] = WEBHOOK_SECRET
        body = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        try:
            tg_payload = json.loads(raw)
        except Exception:
            tg_payload = {"raw": raw}
        self._send_json({"ok": bool(tg_payload.get("ok")), "webhook": target, "telegram": tg_payload})
