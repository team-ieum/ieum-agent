"""요청 루트 span을 열고 correlation ID(X-Trace-Id)·노드 ID(X-Node-Id)를 부착하는 미들웨어."""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import trace

_tracer = trace.get_tracer(__name__)


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        try:
            attributes = {"ieum.trace_id": trace_id}
            node_id = request.headers.get("X-Node-Id")
            if node_id:
                attributes["ieum.node_id"] = node_id
            span = _tracer.start_span(f"{request.method} {request.url.path}", attributes=attributes)
        except Exception:
            span = trace.INVALID_SPAN  # 관측이 요청을 막지 않는다
        # SSE 응답은 헤더 반환 시점에 루트 span이 닫혀 스트리밍 구간은 담지 못한다
        with trace.use_span(span, end_on_exit=True):
            response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
