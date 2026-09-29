from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api.middleware.tracing import TraceIdMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_incoming_trace_id_echoed():
    client = TestClient(_app())
    r = client.get("/ping", headers={"X-Trace-Id": "abc-123"})
    assert r.status_code == 200
    assert r.headers["X-Trace-Id"] == "abc-123"


def test_trace_id_generated_when_absent():
    client = TestClient(_app())
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.headers.get("X-Trace-Id")  # 자체 생성


def _root_span(headers):
    # 전역 TracerProvider는 건드리지 않고 미들웨어의 tracer만 교체한다
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch("api.middleware.tracing._tracer", provider.get_tracer("test")):
        r = TestClient(_app()).get("/ping", headers=headers)
    assert r.status_code == 200
    (span,) = exporter.get_finished_spans()
    return span


def test_root_span_has_trace_and_node_id():
    span = _root_span({"X-Trace-Id": "abc-123", "X-Node-Id": "node-1"})
    assert span.attributes["ieum.trace_id"] == "abc-123"
    assert span.attributes["ieum.node_id"] == "node-1"


def test_root_span_omits_node_id_when_absent():
    span = _root_span({"X-Trace-Id": "abc-123"})
    assert span.attributes["ieum.trace_id"] == "abc-123"
    assert "ieum.node_id" not in span.attributes


def test_handler_span_is_child_of_root_span():
    # 핸들러 안에서 열리는 span(ADK)이 루트의 자식으로 붙어야 속성이 같은 trace에 묶인다
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    app = _app()

    @app.get("/work")
    def work():
        with tracer.start_as_current_span("child"):
            return {"ok": True}

    with patch("api.middleware.tracing._tracer", tracer):
        assert TestClient(app).get("/work").status_code == 200
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["child"].parent.span_id == spans["GET /work"].context.span_id
