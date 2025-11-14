from fastapi import FastAPI, Request
import os

app = FastAPI()

def _summary(path: str):
    return {"ok": True, "service": "hello", "path": path}

# ловим и "/" и полный путь "/api/hello"
@app.get("/")
@app.get("/api/hello")
@app.get("/api/hello/")
async def hello_root(request: Request):
    return _summary(str(request.url.path))

# отладка окружения — тоже на оба пути
@app.get("/debug-env")
@app.get("/api/hello/debug-env")
async def debug_env(request: Request):
    return {
        "path": str(request.url.path),
        "has_pg": bool(os.getenv("POSTGRES_URL")),
        "has_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_secret": bool(os.getenv("WEBHOOK_SECRET")),
        "tz": os.getenv("APP_TZ", "Europe/Helsinki"),
    }
