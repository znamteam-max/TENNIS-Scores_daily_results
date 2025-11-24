# api/diag.py — расширенная диагностика без внешних пакетов (WSGI)
import os, sys, json, importlib.util, socket, ssl
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

def _has(modname: str) -> bool:
    try:
        return importlib.util.find_spec(modname) is not None
    except Exception:
        return False

def _json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    except Exception as e:
        return ('{"ok":false,"err":"json:%s"}' % e).encode("utf-8")

def _mask_env(k: str):
    v = os.getenv(k)
    if not v:
        return None
    if any(x in k.upper() for x in ("TOKEN","KEY","SECRET","PASSWORD")):
        return f"<len:{len(v)}>"
    return v

def _http_get(url: str, headers: dict | None = None, timeout=8):
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore")
    except Exception as e:
        return None, str(e)

def _http_post_json(url: str, payload: dict, timeout=10):
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read().decode("utf-8","ignore")
    except Exception as e:
        return None, str(e)

def app(environ, start_response):
    qs = parse_qs(environ.get("QUERY_STRING", "") or "")
    want_env  = qs.get("env",  ["0"])[0] == "1"
    want_self = qs.get("self", ["0"])[0] == "1"   # проверить /api/webhook GET
    want_tg   = qs.get("tg",   ["0"])[0] == "1"   # спросить getWebhookInfo
    want_db   = qs.get("db",   ["0"])[0] == "1"   # проверить БД (если psycopg есть)
    full      = qs.get("full", ["0"])[0] == "1"

    # базовая сводка
    resp = {
        "ok": True,
        "service": "diag",
        "runtime": "wsgi",
        "python": sys.version,
        "path": environ.get("PATH_INFO", ""),
        "has": {
            "fastapi": _has("fastapi"),
            "httpx":   _has("httpx"),
            "psycopg": _has("psycopg"),
        },
        "tips": [
            "env=1 — показать ENV (замаскировано)",
            "self=1 — запросить GET /api/webhook",
            "tg=1   — Telegram getWebhookInfo (нужен TELEGRAM_BOT_TOKEN)",
            "db=1   — ping БД (нужен psycopg и POSTGRES_URL)",
            "full=1 — выполнить все проверки сразу"
        ],
    }

    if full:
        want_env = want_self = True
        # tg/db попробуем тоже, если есть входные данные
        want_tg = True
        want_db = True

    # ENV
    if want_env:
        resp["env"] = {
            "WEBHOOK_SECRET":     _mask_env("WEBHOOK_SECRET"),
            "TELEGRAM_BOT_TOKEN": _mask_env("TELEGRAM_BOT_TOKEN"),
            "POSTGRES_URL":       _mask_env("POSTGRES_URL") or _mask_env("DATABASE_URL"),
            "APP_TZ":             _mask_env("APP_TZ"),
            "HTTP_HOST":          environ.get("HTTP_HOST"),
        }

    # self-check /api/webhook
    if want_self:
        host = environ.get("HTTP_HOST") or ""
        if host:
            code, body = _http_get(f"https://{host}/api/webhook")
            resp["self_check"] = {"url": f"https://{host}/api/webhook", "code": code, "body_sample": (body or "")[:300]}
        else:
            resp["self_check"] = {"error": "no HTTP_HOST"}

    # Telegram getWebhookInfo
    if want_tg:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if token:
            code, body = _http_get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
            resp["telegram"] = {"code": code, "body": body[:800]}
        else:
            resp["telegram"] = {"error": "TELEGRAM_BOT_TOKEN not set"}

    # DB ping (если psycopg установлен)
    if want_db:
        if _has("psycopg"):
            try:
                import psycopg
                url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or ""
                if not url:
                    resp["db"] = {"error": "POSTGRES_URL not set"}
                else:
                    ok = False
                    with psycopg.connect(url, autocommit=True) as con:
                        with con.cursor() as cur:
                            cur.execute("select 1")
                            ok = True
                    resp["db"] = {"ok": ok}
            except Exception as e:
                resp["db"] = {"ok": False, "error": str(e)}
        else:
            resp["db"] = {"error": "psycopg not installed (add to requirements.txt)"}

    body = _json(resp)
    start_response("200 OK", [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]
