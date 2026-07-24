"""요청별 correlation ID(X-Trace-Id)를 활성 span에 부착하는 미들웨어."""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import trace


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        try:
            span = trace.get_current_span()
            if span and span.is_recording():
                span.set_attribute("ieum.trace_id", trace_id)
        except Exception:
            pass  # 관측이 요청을 막지 않는다
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
