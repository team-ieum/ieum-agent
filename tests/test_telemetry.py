import sys
from unittest.mock import MagicMock, patch

import pytest

from core import telemetry
from core.telemetry import setup_telemetry


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    """main import 등으로 오염된 모듈 전역 _initialized를 각 테스트 전에 리셋한다."""
    telemetry._initialized = False
    yield
    telemetry._initialized = False


def _fake_otel_modules():
    """OTel/OpenInference 미설치 환경에서도 돌도록 가짜 모듈 주입."""
    return patch.dict(sys.modules, {
        "openinference.instrumentation.google_adk": MagicMock(),
        "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(),
    })


def test_setup_noop_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert setup_telemetry() is False


def test_setup_registers_when_endpoint_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:6006/v1/traces")
    with _fake_otel_modules():
        assert setup_telemetry() is True


def test_setup_hides_input_output_content(monkeypatch):
    # LLM 입출력 전문이 OTLP로 유출되면 output_validator 마스킹을 우회해 시크릿이 노출된다.
    # TraceConfig(hide_inputs/hide_outputs)로 메시지 '내용'만 숨기고 메타데이터는 유지해야 한다.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:6006/v1/traces")
    with _fake_otel_modules():
        fake_instrumentor_cls = sys.modules["openinference.instrumentation.google_adk"].GoogleADKInstrumentor
        assert setup_telemetry() is True

    instance = fake_instrumentor_cls.return_value
    instance.instrument.assert_called_once()
    _, kwargs = instance.instrument.call_args
    config = kwargs["config"]
    assert config.hide_inputs is True
    assert config.hide_outputs is True


def test_main_calls_setup_before_routes(monkeypatch):
    # main import 시 setup_telemetry가 호출되는지(부트 훅) 검증
    called = {"v": False}

    def _fake_setup():
        called["v"] = True
        return False

    monkeypatch.setattr("core.telemetry.setup_telemetry", _fake_setup)
    import importlib
    import main
    importlib.reload(main)
    assert called["v"] is True


def test_compute_cost_known_model(monkeypatch):
    from core import telemetry
    monkeypatch.setattr(telemetry, "_completion_cost", lambda **kw: 0.0042)
    cost = telemetry._span_cost_usd(
        model="anthropic/claude-sonnet-4-5", prompt_tokens=100, completion_tokens=50
    )
    assert cost == 0.0042


def test_compute_cost_unknown_model_returns_none(monkeypatch):
    from core import telemetry

    def _raise(**kw):
        raise ValueError("unknown model")

    monkeypatch.setattr(telemetry, "_completion_cost", _raise)
    cost = telemetry._span_cost_usd(model="mystery", prompt_tokens=1, completion_tokens=1)
    assert cost is None  # 계산 실패는 삼키고 None(span은 유지)
