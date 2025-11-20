from __future__ import annotations
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="hello")
handler = app

@app.get("/")
def hello_root():
    return JSONResponse({"ok": True, "service": "hello", "path": "/api/hello/"})

@app.get("/debug-env")
def debug_env():
    keys = ("POSTGRES_URL", "APP_TZ", "WEBHOOK_SECRET")
    env = {k: os.getenv(k, "") for k in keys}
    return JSONResponse({"ok": True, "env": env})
