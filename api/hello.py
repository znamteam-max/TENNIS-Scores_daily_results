from fastapi import FastAPI

app = FastAPI()

# ловим оба варианта путей — со срезом и без
@app.get("")
@app.get("/")
@app.get("/api/hello")
@app.get("/api/hello/")
def hi():
    return {"ok": True}
