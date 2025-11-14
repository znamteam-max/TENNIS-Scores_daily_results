# api/hello.py
from fastapi import FastAPI

app = FastAPI()

# стандартный роут (когда Vercel корректно передаёт root_path)
@app.get("")
@app.get("/")
def hello_root():
    return {"ok": True, "service": "hello", "path": "/"}

# запасной роут (когда путь приходит без root_path)
@app.get("/api/hello")
def hello_abs():
    return {"ok": True, "service": "hello", "path": "/api/hello"}

# полезная проверка окружения
import os
@app.get("/debug-env")
def debug_env():
    return {
        "has_pg": bool(os.getenv("POSTGRES_URL")),
        "has_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_secret": bool(os.getenv("WEBHOOK_SECRET")),
    }
