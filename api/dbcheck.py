# api/dbcheck.py — чистый ASGI, безопасный ping
import json
from db_pg import ensure_schema, ping_db

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return
    try:
        ensure_schema()
        db_ok = ping_db()
        body = json.dumps({"ok": True, "service": "dbcheck", "db": "connected" if db_ok else "fail"}).encode("utf-8")
        status = 200
    except Exception as e:
        body = json.dumps({"ok": False, "service": "dbcheck", "error": str(e)}).encode("utf-8")
        status = 500

    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
