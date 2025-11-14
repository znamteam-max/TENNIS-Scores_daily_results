from fastapi import FastAPI, HTTPException
import os
from db_pg import ensure_schema

app = FastAPI()

@app.get("/")
def db_root():
    if not os.getenv("POSTGRES_URL"):
        raise HTTPException(status_code=500, detail="POSTGRES_URL is not set")
    try:
        ensure_schema()
        return {"ok": True, "service": "dbcheck", "db": "connected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e!s}")
