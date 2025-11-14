from fastapi import FastAPI
import os

app = FastAPI()

@app.get("")
@app.get("/")
def hello():
    return {"ok": True, "service": "hello"}

@app.get("/debug-env")
def debug_env():
    # ничего секретного — только флаги наличия
    return {
        "has_pg": bool(os.getenv("POSTGRES_URL")),
        "has_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_secret": bool(os.getenv("WEBHOOK_SECRET")),
    }
