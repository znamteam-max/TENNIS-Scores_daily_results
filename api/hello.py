from fastapi import FastAPI

app = FastAPI()

@app.get("")
@app.get("/")
def hi():
    return {"ok": True}
