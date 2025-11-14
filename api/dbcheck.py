from fastapi import FastAPI, HTTPException
import os
from db_pg import ensure_schema  # есть в репо

app = FastAPI()

@app.get("/")
def db_root():
    # быстрый ping, чтобы не падать, если переменной нет
    if not os.getenv("POSTGRES_URL"):
        raise HTTPException(status_code=500, detail="POSTGRES_URL is not set")
    try:
        ensure_schema()  # создаст таблицы при необходимости, заодно проверит коннект
        return {"ok": True, "service": "dbcheck", "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e!s}")
