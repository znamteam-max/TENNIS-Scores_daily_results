# api/webhook.py — минимальный ASGI-заглушка
# GET вернёт ok, POST просто отзеркалит JSON (без FastAPI/DB/Telegram)

import json

async def handler(scope, receive, send):
    assert scope["type"] == "http"
    method = scope.get("method", "GET").upper()

    if method == "POST":
        body_bytes = b""
        while True:
            event = await receive()
            if event["type"] == "http.request":
                body_bytes += event.get("body", b"")
                if not event.get("more_body", False):
                    break
        try:
            payload = json.loads(body_bytes.decode("utf-8") or "{}")
        except Exception:
            payload = {"_raw": body_bytes.decode("utf-8", "ignore")}
        res = {"ok": True, "service": "webhook", "received": payload}
    else:
        res = {"ok": True, "service": "webhook", "path": "/api/webhook"}

    body = json.dumps(res, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
