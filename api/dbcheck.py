# /api/dbcheck.py
from __future__ import annotations
import json

from db_pg import ensure_schema, ping_db

async def app(scope, receive, send):
    assert scope["type"] == "http"
    try:
        ensure_schema()
        ok = ping_db()
        body = {"ok": True, "service": "dbcheck", "db": "connected" if ok else "fail"}
        status = 200
    except Exception as e:
        body = {"ok": False, "service": "dbcheck", "error": str(e)}
        status = 500
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    await send({"type":"http.response.start","status":status,"headers":headers})
    await send({"type":"http.response.body","body":payload})
