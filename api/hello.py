# api/hello.py
from fastapi import FastAPI
import os

app = FastAPI()

@app.get("")
@app.get("/")
def hello():
    return {"ok": True, "service": "hello"}

# отладка окружения — два пути, чтобы не было 404
@app.get("/debug-env")
@app.get("/api/hello/debug-env")
def debug_env():
    return {
        "has_pg": bool(os.getenv("POSTGRES_URL")),
        "has_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_secret": bool(os.getenv("WEBHOOK_SECRET")),
        "tz": os.getenv("APP_TZ", "Europe/Helsinki"),
    }
