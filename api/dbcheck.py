from fastapi import FastAPI, HTTPException, Request
import os
from db_pg import ensure_schema

app = FastAPI()

def _ok(path: str): return {"ok": True, "service": "dbcheck", "path": path, "db": "connected"}

@app.get("/")
@app.get("/api/dbcheck")
@app.get("/api/dbcheck/")
def db_root(request: Request):
    if not os.getenv("POSTGRES_URL"):
        raise HTTPException(status_code=500, detail="POSTGRES_URL is not set")
    try:
        ensure_schema()
        return _ok(str(request.url.path))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e!s}")
