import contextlib
import logging

# OpenTelemetry는 google.adk를 끌어오는 어떤 import보다 먼저 배선해야 한다.
from core.telemetry import setup_telemetry
setup_telemetry()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from api.routes import execute, actions, generate, chat, node_catalog, validate
from common.exception import CodedHTTPException
from core.template_registry import validate_registry
from db.mongodb import ensure_indexes
from tools.http_client import close_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 카탈로그 오류는 기동 실패다(spec §5.3) — 경고로 넘기면 첫 생성·검증 요청에서야 500으로 드러난다.
    # Mongo보다 먼저 본다: 카탈로그 문제를 DB 장애와 섞지 않는다.
    validate_registry()
    try:
        await ensure_indexes()
    except Exception:
        logger.warning("인덱스 생성 실패 — 계속 진행합니다.", exc_info=True)
    yield
    await close_http_client()


app = FastAPI(title="ieum-agent", lifespan=lifespan)


@app.exception_handler(CodedHTTPException)
async def coded_http_exception_handler(request: Request, exc: CodedHTTPException):
    """HTTP 에러 본문에도 errorCode를 싣는다. BE는 본문의 errorCode를 우선 보고
    재시도 여부를 판정하므로, 상태코드만 던지면 실패가 UNKNOWN으로 뭉개진다."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "errorCode": exc.error_code},
        headers=exc.headers,
    )

from api.middleware.tracing import TraceIdMiddleware
app.add_middleware(TraceIdMiddleware)

app.include_router(execute.router, prefix="/v1")
app.include_router(actions.router, prefix="/v1")
app.include_router(generate.router, prefix="/v1")
app.include_router(chat.router, prefix="/v1")
app.include_router(node_catalog.router, prefix="/v1")
app.include_router(validate.router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
