# api/diag.py — безопасная диагностика без внешних зависимостей (WSGI)

from __future__ import annotations
import asyncio
import os, sys, json, traceback, urllib.parse, urllib.request, datetime as dt
from zoneinfo import ZoneInfo

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

def _tg_json(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _chat_value_shape(value: str) -> str:
    if not value:
        return "empty"
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        return "telegram_url"
    if value.startswith("@"):
        return "username"
    if value.startswith("-100") and value[4:].isdigit():
        return "channel_or_supergroup_id"
    if value.lstrip("-").isdigit():
        return "numeric_id"
    return "unknown"

def _mask_chat_value(value: str) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return value
    if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
        return value
    if len(value) <= 6:
        return f"<len:{len(value)}>"
    return f"{value[:4]}...{value[-3:]} <len:{len(value)}>"

def _publish_check(token: str, chat_id: str) -> dict:
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is empty"}
    if not chat_id:
        return {"ok": False, "error": "PUBLISH_CHAT_ID is empty"}

    out = {
        "configured": True,
        "value": _mask_chat_value(chat_id),
        "shape": _chat_value_shape(chat_id),
    }
    if out["shape"] == "telegram_url":
        out["hint"] = "Use @channel_username, not https://t.me/... URL"

    try:
        me = _tg_json(token, "getMe")
        bot_id = ((me.get("result") or {}).get("id"))
        out["bot"] = {
            "ok": bool(me.get("ok")),
            "id": bot_id,
            "username": ((me.get("result") or {}).get("username")),
        }
    except Exception as e:
        out["bot"] = {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}
        return out

    try:
        chat = _tg_json(token, "getChat", {"chat_id": chat_id})
        result = chat.get("result") or {}
        out["chat"] = {
            "ok": bool(chat.get("ok")),
            "id": result.get("id"),
            "type": result.get("type"),
            "title": result.get("title"),
            "username": result.get("username"),
        }
    except Exception as e:
        out["chat"] = {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}
        return out

    try:
        member = _tg_json(token, "getChatMember", {"chat_id": chat_id, "user_id": bot_id})
        result = member.get("result") or {}
        out["bot_member"] = {
            "ok": bool(member.get("ok")),
            "status": result.get("status"),
            "can_post_messages": result.get("can_post_messages"),
            "can_send_messages": result.get("can_send_messages"),
        }
    except Exception as e:
        out["bot_member"] = {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}
    return out

def _db_check(pg_url: str) -> dict:
    if not pg_url:
        return {"error": "POSTGRES_URL is empty"}
    if not _has_mod("psycopg"):
        return {"error": "psycopg not installed (add to requirements.txt)"}
    try:
        from db_pg import _conn
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute("select 1")
                row = cur.fetchone()
        return {"ok": True, "select1": row[0] if row else None}
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}

def _today() -> dt.date:
    try:
        return dt.datetime.now(ZoneInfo(os.getenv("APP_TZ") or "Europe/Helsinki")).date()
    except Exception:
        return dt.date.today()

def _summarize_events(data: dict) -> dict:
    data = data or {}
    events_raw = data.get("events", []) or []
    out = {"raw_events": len(events_raw)}
    try:
        from providers import sofascore as ss
        events = ss.normalize_events(data)
        by_group = {}
        by_category = {}
        for e in events:
            by_group[e.get("tour_group") or "unknown"] = by_group.get(e.get("tour_group") or "unknown", 0) + 1
            by_category[e.get("category") or "unknown"] = by_category.get(e.get("category") or "unknown", 0) + 1
        out["normalized_events"] = len(events)
        out["by_group"] = by_group
        out["by_category"] = by_category
        out["men_tournaments"] = len(ss.tournaments_for_tour_group(events, "men"))
        out["women_tournaments"] = len(ss.tournaments_for_tour_group(events, "women"))
    except Exception as e:
        out["summary_error"] = str(e)
    return out

def _cache_check(pg_url: str) -> dict:
    if not pg_url:
        return {"error": "POSTGRES_URL is empty"}
    if not _has_mod("psycopg"):
        return {"error": "psycopg not installed"}
    try:
        from db_pg import _conn
        rows = []
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    select ds, data, updated_at
                    from events_cache
                    order by ds desc
                    limit 5
                    """
                )
                for ds, data, updated_at in cur.fetchall():
                    rows.append({
                        "ds": ds.isoformat() if hasattr(ds, "isoformat") else str(ds),
                        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
                        "summary": _summarize_events(data or {}),
                    })
        return {"ok": True, "today": _today().isoformat(), "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": traceback.format_exc().splitlines()[-1]}

def _source_check() -> dict:
    try:
        from providers import sofascore as ss
        day = _today()
        data = asyncio.run(ss.events_by_date(day)) or {"events": []}
        return {"ok": True, "day": day.isoformat(), "summary": _summarize_events(data)}
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
        publish_chat_id = (
            os.getenv("PUBLISH_CHAT_ID")
            or os.getenv("RESULTS_CHAT_ID")
            or os.getenv("TELEGRAM_PUBLISH_CHAT_ID")
            or ""
        ).strip()

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
                "publish=1 — проверить PUBLISH_CHAT_ID и права бота в канале",
                "db=1   — ping БД (нужен psycopg и POSTGRES_URL)",
                "cache=1 — проверить events_cache",
                "source=1 — проверить загрузку Sofascore",
                "full=1 — выполнить все проверки сразу",
            ],
        }

        do_env  = params.get("env")  is not None or params.get("full") is not None
        do_self = params.get("self") is not None or params.get("full") is not None
        do_tg   = params.get("tg")   is not None or params.get("full") is not None
        do_publish = params.get("publish") is not None or params.get("full") is not None
        do_db   = params.get("db")   is not None or params.get("full") is not None
        do_cache = params.get("cache") is not None
        do_source = params.get("source") is not None

        if do_env:
            data["env"] = _masked_env({
                "WEBHOOK_SECRET": os.getenv("WEBHOOK_SECRET", ""),
                "TELEGRAM_BOT_TOKEN": token or "",
                "PUBLISH_CHAT_ID": _mask_chat_value(publish_chat_id),
                "PUBLISH_CHAT_ID_SHAPE": _chat_value_shape(publish_chat_id),
                "POSTGRES_URL": pg_url or "",
                "APP_TZ": os.getenv("APP_TZ", ""),
                "HTTP_HOST": host,
            })

        if do_self:
            data["self_check"] = _self_check(host)

        if do_tg:
            data["telegram"] = _tg_check(token)

        if do_publish:
            data["publish"] = _publish_check(token, publish_chat_id)

        if do_db:
            data["db"] = _db_check(pg_url)

        if do_cache:
            data["cache"] = _cache_check(pg_url)

        if do_source:
            data["source"] = _source_check()

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
