from fastapi import FastAPI
from api.routes import execute

app = FastAPI(title="ieum-agent")
app.include_router(execute.router, prefix="/v1")

@app.get("/health")
async def health():
    return {"status": "ok"}
