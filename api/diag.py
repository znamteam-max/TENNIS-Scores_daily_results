# api/diag.py — чистый ASGI, без внешних пакетов
import sys, json, importlib.util

def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return
    if scope.get("method") not in ("GET", "HEAD"):
        status = 405
        body = b'{"error":"method not allowed"}'
    else:
        info = {
            "python": sys.version,
            "has": {
                "fastapi": _has("fastapi"),
                "httpx": _has("httpx"),
                "psycopg": _has("psycopg"),
            },
            "hint": "If all False -> requirements.txt not installed by Vercel.",
        }
        body = json.dumps(info).encode("utf-8")
        status = 200

    headers = [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    if scope.get("method") != "HEAD":
        await send({"type": "http.response.body", "body": body})
