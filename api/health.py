# api/health.py — минимальный WSGI, без сторонних зависимостей, всегда 200.

import os, sys, json
from urllib.parse import parse_qs

def _json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:
        return b'{"ok":true}'

def app(environ, start_response):
    # WSGI: environ -> start_response -> iterable[bytes]
    try:
        qs = parse_qs(environ.get("QUERY_STRING", "") or "")
    except Exception:
        qs = {}

    resp = {
        "ok": True,
        "service": "health",
        "runtime": "wsgi",
        "python": sys.version,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "path": environ.get("PATH_INFO", ""),
    }

    # ?env=1 — показать ключевые ENV (секреты не раскрываем, только длину)
    if qs.get("env", ["0"])[0] == "1":
        def _mask(k):
            v = os.getenv(k)
            if not v: return None
            if any(x in k for x in ("TOKEN","KEY","SECRET","PASSWORD")):
                return f"<len:{len(v)}>"
            return v
        resp["env"] = {
            "WEBHOOK_SECRET": _mask("WEBHOOK_SECRET"),
            "TELEGRAM_BOT_TOKEN": _mask("TELEGRAM_BOT_TOKEN"),
            "POSTGRES_URL": _mask("POSTGRES_URL"),
            "DATABASE_URL": _mask("DATABASE_URL"),
            "APP_TZ": _mask("APP_TZ"),
        }

    body = _json(resp)
    start_response("200 OK", [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]
