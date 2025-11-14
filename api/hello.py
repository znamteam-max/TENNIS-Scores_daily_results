# api/hello.py
from fastapi import FastAPI
app = FastAPI()

@app.get("")
@app.get("/")
def hello():
    return {"ok": True, "service": "hello"}
