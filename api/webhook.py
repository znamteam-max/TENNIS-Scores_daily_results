# /api/webhook.py — чистый ASGI echo (GET/POST), без Telegram/БД/импортов
import json

async def app(scope, receive, send):
    assert scope["type"] == "http"
    method = scope.get("method", "GET").upper()

    if method == "POST":
        data = b""
        while True:
            event = await receive()
            if event["type"] == "http.request":
                data += event.get("body", b"")
                if not event.get("more_body", False):
                    break
        try:
            payload = json.loads(data.decode("utf-8") or "{}")
        except Exception:
            payload = {"_raw": data.decode("utf-8", "ignore")}
        res = {"ok": True, "service": "webhook", "received": payload}
    else:
        res = {"ok": True, "service": "webhook", "path": "/api/webhook"}

    body = json.dumps(res, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
