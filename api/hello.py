# /api/hello.py — чистый ASGI, переменная "app"
async def app(scope, receive, send):
    assert scope["type"] == "http"
    body = b'{"ok":true,"service":"hello","path":"/api/hello/"}'
    headers = [(b"content-type", b"application/json")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
