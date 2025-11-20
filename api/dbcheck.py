# api/dbcheck.py — минимальный ASGI (без доступа к БД) чисто для проверки рантайма
async def handler(scope, receive, send):
    assert scope["type"] == "http"
    body = b'{"ok":true,"service":"dbcheck","note":"ASGI diag, no DB import"}'
    headers = [(b"content-type", b"application/json")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
