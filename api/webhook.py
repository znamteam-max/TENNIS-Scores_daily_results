# api/webhook.py — zero deps, GET=health, POST=ack; never 500

def _get_env(name: str, default: str = "") -> str:
    try:
        import os
        return os.getenv(name, default)
    except Exception:
        return default

async def _read_body(receive) -> bytes:
    chunks, more = [], True
    while more:
        ev = await receive()
        if ev.get("body"):
            chunks.append(ev["body"])
        more = ev.get("more_body", False)
    return b"".join(chunks)

def _get_header(scope, key: str) -> str:
    key = key.lower()
    try:
        for k, v in scope.get("headers", []):
            if k.decode(errors="ignore").lower() == key:
                return v.decode(errors="ignore")
    except Exception:
        pass
    return ""

async def _json(send, status: int, payload: dict):
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

        method = (scope.get("method") or "GET").upper()
        if method == "GET":
            await _json(send, 200, {"ok": True, "service": "webhook", "path": "/api/webhook"})
            return

        if method != "POST":
            await _json(send, 405, {"ok": False, "error": "method not allowed"})
            return

        # Optional secret (temporarily unset it in Vercel for manual tests)
        secret = _get_env("WEBHOOK_SECRET", "")
        if secret:
            tok = _get_header(scope, "x-telegram-bot-api-secret-token")
            if tok != secret:
                await _json(send, 403, {"error": "forbidden"})
                return

        # Read body safely (ignore parse errors)
        try:
            raw = await _read_body(receive)
            try:
                import json as _jsonlib
                _ = _jsonlib.loads((raw or b"{}").decode("utf-8", "ignore") or "{}")
            except Exception:
                pass
        except Exception:
            pass

        await _json(send, 200, {"ok": True})
    except Exception as e:
        await _json(send, 200, {"ok": True, "service": "webhook", "note": f"caught:{type(e).__name__}"})
