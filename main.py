import contextlib
from fastapi import FastAPI
from api.routes import execute, generate, modify, chat
from tools.http_client import close_http_client


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_http_client()


app = FastAPI(title="ieum-agent", lifespan=lifespan)
app.include_router(execute.router, prefix="/v1")
app.include_router(generate.router, prefix="/v1")
app.include_router(modify.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
