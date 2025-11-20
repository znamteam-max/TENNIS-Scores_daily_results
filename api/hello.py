# api/hello.py — чистый ASGI

from __future__ import annotations
import json

async def _json(send, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [[b"content-type", b"application/json; charset=utf-8"]],
    })
    await send({"type": "http.response.body", "body": body})

async def app(scope, receive, send):
    if scope["type"] != "http":
        await _json(send, 200, {"ok": True, "note": "not http"})
        return
    await _json(send, 200, {"ok": True, "service": "hello", "path": "/api/hello/"})

handler = app
