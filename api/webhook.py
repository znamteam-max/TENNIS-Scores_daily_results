# api/webhook.py — zero-dep ASGI webhook (GET=health, POST=ack)

async def _read_body(receive) -> bytes:
    chunks, more = [], True
    while more:
        ev = await receive()
        if ev.get("body"): chunks.append(ev["body"])
        more = ev.get("more_body", False)
    return b"".join(chunks)

async def _json(send, status: int, payload: dict):
    import json
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})

def _get_header(scope, key: str) -> str:
    key = key.lower()
    try:
        for k, v in scope.get("headers", []):
            if k.decode().lower() == key:
                return v.decode()
    except Exception:
        pass
    return ""

# читаем только stdlib os — на Vercel он точно есть
try:
    import os
except Exception:
    os = None

async def app(scope, receive, send):
    if scope.get("type") != "http":
        await _json(send, 200, {"ok": True, "note": "not http"})
        return

    method = scope.get("method", "GET").upper()

    if method == "GET":
        await _json(send, 200, {"ok": True, "service": "webhook", "path": "/api/webhook"})
        return

    if method != "POST":
        await _json(send, 405, {"ok": False, "error": "method not allowed"})
        return

    # Если задан WEBHOOK_SECRET — проверим заголовок Telegram
    secret = (os.getenv("WEBHOOK_SECRET") if os else "") or ""
    if secret:
        token = _get_header(scope, "x-telegram-bot-api-secret-token")
        if token != secret:
            await _json(send, 403, {"error": "forbidden"})
            return

    # Примем апдейт и просто подтвердим (функционал вернём после оживления)
    try:
        raw = await _read_body(receive)
        # Можно распарсить для дебага (stdlib json):
        import json as _jsonlib  # точно есть
        _ = _jsonlib.loads(raw.decode("utf-8") or "{}")
    except Exception:
        pass

    await _json(send, 200, {"ok": True})

handler = app
