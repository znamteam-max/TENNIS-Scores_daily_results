# api/diag.py — WSGI-диагностика без внешних зависимостей
import os, sys, json, importlib.util
from urllib.parse import parse_qs

def _has(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except Exception:
        return False

def _json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:
        return b'{"ok":false,"err":"json"}'

def app(environ, start_response):
    qs = parse_qs(environ.get("QUERY_STRING", "") or "")
    show_env = qs.get("env", ["0"])[0] == "1"

    resp = {
        "ok": True,
        "service": "diag",
        "runtime": "wsgi",
        "python": sys.version,
        "path": environ.get("PATH_INFO", ""),
        "has": {
            # покажет, подхватил ли Vercel зависимости, когда вернем requirements.txt
            "fastapi": _has("fastapi"),
            "httpx": _has("httpx"),
            "psycopg": _has("psycopg"),
        }
    }

    if show_env:
        def _mask(k):
            v = os.getenv(k)
            if not v:
                return None
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
