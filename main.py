import contextlib
import logging

# OpenTelemetry는 google.adk를 끌어오는 어떤 import보다 먼저 배선해야 한다.
from core.telemetry import setup_telemetry
setup_telemetry()

from fastapi import FastAPI
from api.routes import execute, generate, modify, chat
from db.mongodb import ensure_indexes, seed_node_templates
from tools.http_client import close_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_indexes()
    except Exception:
        logger.warning("인덱스 생성 실패 — 계속 진행합니다.", exc_info=True)
    try:
        await seed_node_templates()
    except Exception:
        logger.warning("노드 템플릿 동기화 실패 — 계속 진행합니다.", exc_info=True)
    yield
    await close_http_client()


app = FastAPI(title="ieum-agent", lifespan=lifespan)

from api.middleware.tracing import TraceIdMiddleware
app.add_middleware(TraceIdMiddleware)

app.include_router(execute.router, prefix="/v1")
app.include_router(generate.router, prefix="/v1")
app.include_router(modify.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
