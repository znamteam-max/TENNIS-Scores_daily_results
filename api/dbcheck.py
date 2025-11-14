from fastapi import FastAPI
import os, psycopg

app = FastAPI()

@app.get("")
@app.get("/")
def check():
    url = os.getenv("POSTGRES_URL")
    assert url, "POSTGRES_URL is empty"
    with psycopg.connect(url) as con:
        with con.cursor() as cur:
            cur.execute("select version()")
            ver = cur.fetchone()[0]
            cur.execute("select current_database(), current_user")
            db, usr = cur.fetchone()
    return {"ok": True, "version": ver, "db": db, "user": usr}
