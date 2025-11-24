# api/diag.py — безопасная диагностика без внешних зависимостей (WSGI)

from __future__ import annotations
import os, sys, json, traceback, urllib.parse, urllib.request, datetime as dt

def _json(obj: dict, status: str = "200 OK"):
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Cache-Control", "no-store, max-age=0"),
        ("Access-Control-Allow-Origin", "*"),
    ]
    return status, headers, body.encode("utf-8")

def _has_mod(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False

def _masked_env(env: dict) -> dict:
    # Маскируем чувствительные значения
    mask_keys = {"POSTGRES_URL", "DATABASE_URL", "TELEGRAM_BOT_TOKEN", "WEBHOOK_SECRET"}
    out = {}
    for k, v in env.items():
        if k in mask_keys and v:
            out[k] = f"<len:{len(v)}>"
        else:
            out[k] = v
    return out

def _self_check(host: str) -> dict:
    url = f"https://{host}/api/webhook"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            code = r.getcode()
            b = r.read(512).decode("utf-8", "replace")
            return {"url": url, "code": code, "body_sample": b}
    except Exception as e:
        return {"url": url, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}

def _tg_check(token: str) -> dict:
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN is empty"}
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            code = r.getcode()
            body = r.read().decode("utf-8", "replace")
            return {"code": code, "body": body}
    except Exception as e:
        return {"error": str(e), "trace": traceback.format_exc().splitlines()[-1]}

def _db_check(pg_url: str) -> dict:
    if not pg_url:
        return {"error": "POSTGRES_URL is empty"}
    if not _has_mod("psycopg"):
        return {"error": "psycopg not installed (add to requirements.txt)"}
    try:
        import psycopg
        with psycopg.connect(pg_url, autocommit=True) as con:
            with con.cursor() as cur:
                cur.execute("select 1")
                row = cur.fetchone()
        return {"ok": True, "select1": row[0] if row else None}
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}

def app(environ, start_response):
    try:
        qs = environ.get("QUERY_STRING", "")
        params = dict(urllib.parse.parse_qsl(qs))

        host = environ.get("HTTP_HOST", "")
        path = environ.get("PATH_INFO", "")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        pg_url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")

        # Базовый ответ
        data = {
            "ok": True,
            "service": "diag",
            "runtime": "wsgi",
            "python": sys.version,
            "path": path,
            "has": {
                "fastapi": _has_mod("fastapi"),
                "httpx": _has_mod("httpx"),
                "psycopg": _has_mod("psycopg"),
            },
            "tips": [
                "env=1 — показать ENV (замаскировано)",
                "self=1 — запросить GET /api/webhook",
                "tg=1   — Telegram getWebhookInfo (нужен TELEGRAM_BOT_TOKEN)",
                "db=1   — ping БД (нужен psycopg и POSTGRES_URL)",
                "full=1 — выполнить все проверки сразу",
            ],
        }

        do_env  = params.get("env")  is not None or params.get("full") is not None
        do_self = params.get("self") is not None or params.get("full") is not None
        do_tg   = params.get("tg")   is not None or params.get("full") is not None
        do_db   = params.get("db")   is not None or params.get("full") is not None

        if do_env:
            data["env"] = _masked_env({
                "WEBHOOK_SECRET": os.getenv("WEBHOOK_SECRET", ""),
                "TELEGRAM_BOT_TOKEN": token or "",
                "POSTGRES_URL": pg_url or "",
                "APP_TZ": os.getenv("APP_TZ", ""),
                "HTTP_HOST": host,
            })

        if do_self:
            data["self_check"] = _self_check(host)

        if do_tg:
            data["telegram"] = _tg_check(token)

        if do_db:
            data["db"] = _db_check(pg_url)

        status, headers, body = _json(data)
        start_response(status, headers)
        return [body]

    except Exception as e:
        status, headers, body = _json({
            "ok": False,
            "service": "diag",
            "error": str(e),
            "trace": traceback.format_exc(),
        }, status="500 Internal Server Error")
        start_response(status, headers)
        return [body]
