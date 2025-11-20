# api/dbcheck.py — чистый ASGI, без fastapi/psycopg

from __future__ import annotations
import json

# пробуем импортировать DB-утилиты, но не требуем их
try:
    from db_pg import ensure_schema, ping_db  # type: ignore
    _HAVE_DB = True
except Exception:
    _HAVE_DB = False

async def _json(send, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json; charset=utf-8"],
        ],
    })
    await send({"type": "http.response.body", "body": body})

async def app(scope, receive, send):
    if scope["type"] != "http":
        await _json(send, 200, {"ok": True, "note": "not http"})
        return
    if scope["method"] != "GET":
        await _json(send, 405, {"ok": False, "error": "method not allowed"})
        return
    try:
        if _HAVE_DB:
            ensure_schema()
            ok = bool(ping_db())
            await _json(send, 200, {"ok": True, "service": "dbcheck", "db": "connected" if ok else "fail"})
        else:
            await _json(send, 200, {"ok": True, "service": "dbcheck", "mode": "no-db-packages"})
    except Exception as e:
        await _json(send, 500, {"ok": False, "service": "dbcheck", "error": str(e)})

# для Vercel
handler = app
