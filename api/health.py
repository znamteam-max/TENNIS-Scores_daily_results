# api/health.py — единая диагностика. Никаких внешних импортов, всегда 200.

import os, sys, json, traceback, importlib, pkgutil
from pathlib import Path
from urllib.parse import parse_qs

# --- утилиты ---------------------------------------------------------------
def _b(s: str) -> bytes:
    return s.encode("utf-8")

async def _send_json(send, status: int, payload: dict):
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception:
        body = b'{"ok":true}'
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})

def _qs(scope) -> dict:
    try:
        raw = scope.get("query_string") or b""
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
    except Exception:
        return {}

def _probe_import(name: str):
    try:
        importlib.invalidate_caches()
        importlib.import_module(name)
        return {"module": name, "ok": True}
    except Exception as e:
        return {
            "module": name,
            "ok": False,
            "err": f"{type(e).__name__}: {e}",
            "trace": "".join(traceback.format_exception_only(type(e), e)).strip(),
        }

def _has(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False

def _env_summary(keys):
    out = {}
    for k in keys:
        v = os.getenv(k)
        if not v:
            out[k] = None
            continue
        # токены не светим
        if "TOKEN" in k or "KEY" in k or "SECRET" in k or "PASSWORD" in k:
            out[k] = f"<len:{len(v)}>"
        else:
            out[k] = v
    return out

def _ls_api():
    try:
        p = Path("/var/task/api")
        if not p.exists():
            p = Path(__file__).parent
        items = []
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix == ".py":
                try:
                    items.append({"name": f.name, "size": f.stat().st_size})
                except Exception:
                    items.append({"name": f.name})
        return {"dir": str(p), "files": items}
    except Exception as e:
        return {"err": f"{type(e).__name__}: {e}"}

# --- основной ASGI ---------------------------------------------------------
async def asgi(scope, receive, send):
    if scope.get("type") != "http":
        await _send_json(send, 200, {"ok": True, "note": "not http"})
        return

    method = (scope.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        await _send_json(send, 405, {"ok": False, "error": "method not allowed"})
        return

    q = _qs(scope)

    # базовая сводка
    resp = {
        "ok": True,
        "service": "health",
        "python": sys.version,
        "cwd": os.getcwd(),
        "pid": os.getpid(),
    }

    # quick modes через query:
    # ?probe=1 — проверка наличия fastapi/httpx/psycopg
    if q.get("probe") == "1":
        resp["has"] = {
            "fastapi": _has("fastapi"),
            "httpx": _has("httpx"),
            "psycopg": _has("psycopg"),
        }

    # ?env=1 — показать важные ENV (без токенов)
    if q.get("env") == "1":
        resp["env"] = _env_summary([
            "WEBHOOK_SECRET",
            "TELEGRAM_BOT_TOKEN",
            "POSTGRES_URL",
            "DATABASE_URL",
            "APP_TZ",
        ])

    # ?ls=1 — показать, какие .py реально есть в api/
    if q.get("ls") == "1":
        resp["api_dir"] = _ls_api()

    # ?import=api.webhook — безопасная попытка импортировать модуль и поймать его ошибку
    mod = q.get("import")
    if mod:
        resp["import_test"] = _probe_import(mod)

    # ?file=api/webhook.py — попытка прочитать первые 200 байт (чтобы убедиться, что файл реально задеплоен)
    fpath = q.get("file")
    if fpath:
        try:
            p = Path("/var/task") / fpath
            if not p.exists():
                p = Path(__file__).parent.parent / fpath  # fallback
            head = ""
            if p.exists() and p.is_file():
                head = p.read_bytes()[:200].decode("utf-8", "ignore")
            resp["file_probe"] = {"path": str(p), "exists": p.exists(), "head": head}
        except Exception as e:
            resp["file_probe"] = {"path": fpath, "err": f"{type(e).__name__}: {e}"}

    await _send_json(send, 200, resp)

# Vercel ищет переменные "handler" или "app"
handler = asgi
app = asgi
