from fastapi import FastAPI
from fastapi.testclient import TestClient

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
