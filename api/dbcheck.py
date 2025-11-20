from __future__ import annotations
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from db_pg import ensure_schema, ping_db

app = FastAPI(title="dbcheck")
handler = app

@app.get("/")
def dbcheck_root():
    try:
        ensure_schema()
        ok = ping_db()
        return JSONResponse({"ok": True, "service": "dbcheck", "db": "connected" if ok else "fail"})
    except Exception as e:
        return JSONResponse({"ok": False, "service": "dbcheck", "error": str(e)}, status_code=500)
