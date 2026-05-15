from fastapi import FastAPI
from api.routes import execute, generate, modify, chat

app = FastAPI(title="ieum-agent")
app.include_router(execute.router, prefix="/v1")
app.include_router(generate.router, prefix="/v1")
app.include_router(modify.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
