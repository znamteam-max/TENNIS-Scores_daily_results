# api/hello.py — чистый ASGI без FastAPI
import json

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return
    if scope.get("method") not in ("GET", "HEAD"):
        status = 405
        body = b'{"error":"method not allowed"}'
    else:
        body = json.dumps({"ok": True, "service": "hello", "path": "/api/hello/"}).encode("utf-8")
        status = 200

    headers = [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    if scope.get("method") != "HEAD":
        await send({"type": "http.response.body", "body": body})
