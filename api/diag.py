# api/diag.py — zero deps, safe 200 even on exceptions

async def _json(send, status, payload: dict):
    try:
        import json
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        body = b'{"ok":true}'
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})

async def handler(scope, receive, send):
    try:
        if scope.get("type") != "http":
            await _json(send, 200, {"ok": True, "note": "not http"})
            return
        await _json(send, 200, {"ok": True, "service": "diag"})
    except Exception as e:
        await _json(send, 200, {"ok": True, "service": "diag", "note": f"caught:{type(e).__name__}"})
