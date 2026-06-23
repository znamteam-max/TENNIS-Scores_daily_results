from __future__ import annotations

import importlib
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse


HANDLER_ROUTES = {
    "/api/card_preview": "card_preview",
    "/api/fantasy_matches": "fantasy_matches",
    "/api/poll": "poll",
    "/api/set_webhook": "set_webhook",
    "/api/webhook": "webhook",
}

WSGI_ROUTES = {
    "/api/diag": "diag",
    "/api/health": "health",
}


def _json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _module(name: str):
    try:
        return importlib.import_module(f"api.{name}")
    except ModuleNotFoundError as exc:
        if exc.name not in {"api", f"api.{name}"}:
            raise
        return importlib.import_module(name)


def _route_path(raw_path: str) -> str:
    path = urlparse(raw_path).path.rstrip("/")
    return path or "/api"


def _delegate_handler(request: BaseHTTPRequestHandler, module_name: str) -> bool:
    module = _module(module_name)
    target = getattr(module, "handler", None)
    method = getattr(target, f"do_{request.command}", None) if target else None
    if not method:
        return False
    method(request)
    return True


def _delegate_wsgi(request: BaseHTTPRequestHandler, module_name: str) -> bool:
    module = _module(module_name)
    app = getattr(module, "app", None)
    if not app:
        return False

    parsed = urlparse(request.path)
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": request.command,
        "PATH_INFO": parsed.path,
        "QUERY_STRING": parsed.query,
        "HTTP_HOST": request.headers.get("host", ""),
        "CONTENT_LENGTH": request.headers.get("content-length", ""),
        "CONTENT_TYPE": request.headers.get("content-type", ""),
    }
    body_parts = app(environ, start_response)
    status_text = str(captured.get("status") or "200 OK")
    try:
        status_code = int(status_text.split()[0])
    except Exception:
        status_code = 200
    request.send_response(status_code)
    for key, value in captured.get("headers") or []:
        request.send_header(str(key), str(value))
    request.end_headers()
    for part in body_parts or []:
        request.wfile.write(part if isinstance(part, bytes) else str(part).encode("utf-8"))
    return True


class handler(BaseHTTPRequestHandler):
    def _dispatch(self) -> None:
        path = _route_path(self.path)
        if path in HANDLER_ROUTES:
            if _delegate_handler(self, HANDLER_ROUTES[path]):
                return
            _json(self, {"ok": False, "error": "method_not_allowed", "path": path}, status=405)
            return
        if path in WSGI_ROUTES:
            if _delegate_wsgi(self, WSGI_ROUTES[path]):
                return
            _json(self, {"ok": False, "error": "wsgi_app_not_found", "path": path}, status=500)
            return
        if path in {"/api", "/api/index"}:
            _json(self, {"ok": True, "service": "tennis-api", "routes": sorted([*HANDLER_ROUTES, *WSGI_ROUTES])})
            return
        _json(self, {"ok": False, "error": "not_found", "path": path}, status=404)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._dispatch()
