import sys, json, pkgutil

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return
    pkgs = sorted([m.name for m in pkgutil.iter_modules()])
    body = json.dumps({
        "python": sys.version,
        "packages_sample": pkgs[:50]  # первые 50, чтобы не раздувать ответ
    }).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})
