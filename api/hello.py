# api/hello.py
from fastapi import FastAPI
import os

app = FastAPI()

# На Vercel этот файл обслуживается по /api/hello
# Поэтому внутри — только "/" и "/debug-env".
@app.get("/")
def hello_root():
    return {"ok": True, "service": "hello", "path": "/api/hello"}

@app.get("/debug-env")
def debug_env():
    return {
        "has_pg": bool(os.getenv("POSTGRES_URL")),
        "has_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_secret": bool(os.getenv("WEBHOOK_SECRET")),
        "tz": os.getenv("APP_TZ", "Europe/Helsinki"),
        "python": os.getenv("PYTHON_VERSION", "auto"),
    }
