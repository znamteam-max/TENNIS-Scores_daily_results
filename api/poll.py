from __future__ import annotations

import asyncio
import json
import os
import datetime as dt
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from gha_worker import run_once, sync_fantasy_results


CRON_SECRET = os.getenv("CRON_SECRET", "").strip()


def _parse_day(value):
    try:
        return dt.date.fromisoformat(str(value))
    except Exception:
        return None


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
            query = parse_qs(urlparse(self.path).query)
            days = []
            for raw in query.get("day", []) + ",".join(query.get("days", [])).split(","):
                day = _parse_day(raw.strip())
                if day:
                    days.append(day)
            include_yesterday = (query.get("include_yesterday") or ["0"])[0].lower() in {"1", "true", "yes", "on"}
            fantasy_config = {
                "url": (query.get("fantasy_url") or [""])[0],
                "key": (query.get("fantasy_key") or [""])[0],
                "admin_id": (query.get("fantasy_admin_id") or [""])[0],
                "actions": (query.get("fantasy_actions") or [""])[0],
            }
            fantasy_config = {k: v for k, v in fantasy_config.items() if v}
            has_fantasy_key = bool(fantasy_config.get("key") or os.getenv("FANTASY_ADMIN_ACTION_KEY", "").strip())
            explicit_fantasy_actions = bool(fantasy_config.get("actions"))
            if has_fantasy_key and not explicit_fantasy_actions and dt.datetime.utcnow().minute % 2 == 1:
                fantasy_config["actions"] = "refresh_matches"
                result = {
                    "sent": 0,
                    "sources": [{"skipped": "source_fetch", "reason": "fantasy_refresh_phase"}],
                    "fantasy": sync_fantasy_results(fantasy_config),
                }
            else:
                if has_fantasy_key and not explicit_fantasy_actions:
                    fantasy_config["actions"] = "send_notification_queue"
                result = asyncio.run(run_once(days or None, include_yesterday=include_yesterday, fantasy_config=fantasy_config)) or {}
            payload = {"ok": True, **result}
        except Exception as exc:
            print(f"[ERR] cron poll failed: {exc}")
            payload = {"ok": False, "error": str(exc)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
