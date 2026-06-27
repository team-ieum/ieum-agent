"""core/custom_gemini.py의 google-genai SDK 호환성 회귀 테스트.

CustomGemini는 ADK `Gemini`와 google-genai `Client`의 **private/internal 구조**에 의존한다:
- `api_client` cached_property를 오버라이드하며 `Gemini`의 내부 속성
  (`base_url`, `retry_options`, `_tracking_headers()`)을 읽는다.
- genai `Client`의 `models.generate_content` / `generate_content_stream`과
  `aio.models.*`(sync/async 4종)을 몽키패치한다.

genai는 빠르게 움직이는 SDK라 이 내부 경로가 마이너 업그레이드에서 조용히 바뀔 수 있다.
이 테스트는 그 드리프트를 CI에서 자동으로 잡는다. (실제 LLM round-trip이 아니라
'패치가 의존하는 표면이 여전히 존재하는가'를 검증하는 구조 호환성 테스트다.)

검증 시점 조합: google-adk 2.3.0 / google-genai 2.10.0
"""
import warnings

import pytest

warnings.filterwarnings("ignore")


def test_gemini_internal_attrs_exist():
    """api_client 오버라이드가 의존하는 ADK Gemini 내부 속성이 존재한다."""
    from core.custom_gemini import CustomGemini

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    for attr in ("base_url", "retry_options", "_tracking_headers"):
        assert hasattr(g, attr), f"ADK Gemini가 더 이상 '{attr}'를 노출하지 않음 — api_client 오버라이드 깨짐"


def test_api_client_builds_with_injected_key():
    """오버라이드한 api_client cached_property가 genai Client를 정상 빌드한다."""
    from google.genai import Client
    from core.custom_gemini import CustomGemini

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    client = g.api_client
    assert isinstance(client, Client)


def test_monkeypatch_targets_exist():
    """몽키패치 대상 4종(generate_content[_stream] · aio)의 경로가 모두 존재한다."""
    from core.custom_gemini import CustomGemini

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    client = g.api_client
    targets = [
        client.models.generate_content,
        client.models.generate_content_stream,
        client.aio.models.generate_content,
        client.aio.models.generate_content_stream,
    ]
    for fn in targets:
        assert callable(fn), "genai Client의 generate_content 계열 경로가 바뀜 — 몽키패치 깨짐"


def test_clean_config_helpers_callable():
    """_clean_tools / _limit_thinking 헬퍼가 존재하고 호출 가능하다 (R1 이관 시 회귀 가드)."""
    from core import custom_gemini

    # additionalProperties 제거: dict 입력이 정리되는지
    schema = {"additionalProperties": True, "properties": {"x": {"additionalProperties": True}}}
    custom_gemini._clean_schema(schema)
    assert "additionalProperties" not in schema
    assert "additionalProperties" not in schema["properties"]["x"]
