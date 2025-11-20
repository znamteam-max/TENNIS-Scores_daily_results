# api/diag.py — чистый ASGI
import sys, json

async def _json(send, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({"type":"http.response.start","status":status,
                "headers":[[b"content-type", b"application/json; charset=utf-8"]]})
    await send({"type":"http.response.body","body":body})

async def app(scope, receive, send):
    if scope["type"] != "http":
        await _json(send, 200, {"ok": True}); return
    has = {}
    for m in ("fastapi","httpx","psycopg"):
        try: __import__(m); has[m]=True
        except Exception: has[m]=False
    await _json(send, 200, {
        "python": sys.version,
        "has": has,
        "hint": "If all False -> requirements.txt not installed by Vercel."
    })

handler = app
