# api/ping.py  — minimal ASGI, zero deps, always 200

async def handler(scope, receive, send):
    status = 200
    if scope.get("type") != "http":
        body = b'{"ok":true,"note":"not http"}'
    else:
        body = b'{"ok":true,"service":"ping"}'
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})
