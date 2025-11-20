# api/diag.py — zero-dep ASGI

async def _json(send, status, payload: dict):
    body = ('{"ok":true,"service":"diag"}').encode("utf-8")
    try:
        import json  # stdlib, точно есть
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        pass
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})

async def app(scope, receive, send):
    if scope.get("type") != "http":
        await _json(send, 200, {"ok": True, "note": "not http"})
        return
    await _json(send, 200, {"ok": True, "service": "diag"})

handler = app
