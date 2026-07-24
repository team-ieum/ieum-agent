"""OpenTelemetry 부트스트랩. google.adk import 이전에 setup_telemetry()를 호출해야 한다.

OTEL_EXPORTER_OTLP_ENDPOINT가 없으면 no-op(관측이 실행을 막지 않는다).
"""
import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def _completion_cost(**kwargs):
    """litellm.completion_cost 얇은 래퍼(테스트에서 monkeypatch 지점)."""
    import litellm
    return litellm.completion_cost(**kwargs)


def _span_cost_usd(model: str, prompt_tokens: int, completion_tokens: int):
    """token→USD cost. 계산 실패(미지 모델 등)는 None 반환(span은 유지)."""
    try:
        return _completion_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except Exception:
        return None


def setup_telemetry() -> bool:
    """OTel TracerProvider + OTLP exporter + ADK instrumentor를 등록한다.

    endpoint 미설정이면 아무것도 하지 않고 False. 성공 등록 시 True. 멱등.
    """
    global _initialized
    if _initialized:
        return True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        GoogleADKInstrumentor().instrument(tracer_provider=provider)
        _initialized = True
        logger.info("OpenTelemetry 트레이싱 활성화 → %s", endpoint)
        return True
    except Exception:
        logger.warning("OpenTelemetry 초기화 실패 — 트레이싱 없이 계속합니다.", exc_info=True)
        return False
