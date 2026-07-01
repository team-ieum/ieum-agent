"""core/custom_gemini.py의 google-genai SDK 호환성 회귀 테스트.

CustomGemini는 ADK `Gemini`의 **internal 구조**에 의존한다:
- `api_client` cached_property를 오버라이드하며 `Gemini`의 내부 속성
  (`base_url`, `retry_options`, `_tracking_headers()`)을 읽어 api_key를 동적 주입한다.
- (R1d) thinking 예산은 `generate_content_async`를 오버라이드해 `LlmRequest.config`에
  주입한다. genai client 몽키패치는 제거됐다.

genai/ADK는 빠르게 움직이는 SDK라 이 내부 경로가 업그레이드에서 조용히 바뀔 수 있다.
이 테스트는 그 드리프트를 CI에서 자동으로 잡는다(실제 LLM round-trip 아님).

검증 시점 조합: google-adk 2.3.0 / google-genai 2.10.0
"""
import warnings
from unittest.mock import patch

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


def test_api_client_not_monkeypatched():
    """R1d: api_client는 더 이상 genai Client의 generate_content를 몽키패치하지 않는다.
    (thinking 주입은 generate_content_async 오버라이드로 이전됨.) 몽키패치가 되살아나면
    genai 결합이 재유입된 것이라 잡는다."""
    from google.genai import Client
    from core.custom_gemini import CustomGemini

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    client = g.api_client
    fresh = Client(api_key="test-key")
    # 래핑됐다면 함수 이름이 wrapped_* 로 바뀐다. 원본과 동일 이름이어야 한다(미패치).
    assert client.aio.models.generate_content.__name__ == fresh.aio.models.generate_content.__name__


@pytest.mark.asyncio
async def test_generate_content_async_injects_thinking():
    """R1d: generate_content_async 오버라이드가 LlmRequest.config에 thinking 예산을 주입한 뒤
    상위에 위임한다. (API 호출 없이 상위를 stub해 주입만 검증.)"""
    from types import SimpleNamespace
    from google.genai import types as genai_types
    from google.adk.models.google_llm import Gemini
    from core.custom_gemini import CustomGemini, _THINKING_BUDGET

    captured = {}

    async def fake_super(self, llm_request, stream=False):
        captured["thinking"] = llm_request.config.thinking_config
        captured["stream"] = stream
        if False:
            yield  # async generator로 만든다

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    llm_request = SimpleNamespace(config=genai_types.GenerateContentConfig())

    with patch.object(Gemini, "generate_content_async", fake_super):
        async for _ in g.generate_content_async(llm_request, stream=True):
            pass

    assert captured["thinking"] is not None, "thinking_config 미주입"
    assert captured["thinking"].thinking_budget == _THINKING_BUDGET
    assert captured["stream"] is True, "stream 인자 전달 안 됨"


@pytest.mark.asyncio
async def test_thinking_config_reaches_genai_client():
    """R1d 실 forwarding 가드 — 위 테스트는 super()를 stub해 '주입'만 본다. 이 테스트는
    ADK Gemini.generate_content_async를 실제로 태우고 genai client 경계
    (`api_client.aio.models.generate_content`)만 stub해, 주입한 thinking_config가 ADK를
    관통해 genai 호출의 config로 전달되는지 검증한다. ADK 업그레이드가 llm_request.config
    forwarding을 바꾸면(예: config를 안 넘김) thinking 예산이 조용히 무력화되는데, 그때
    captured config에 thinking_config가 없어 이 테스트가 실패한다."""
    from unittest.mock import patch
    from google.genai import types as genai_types
    from google.adk.models.llm_request import LlmRequest
    from core.custom_gemini import CustomGemini, _THINKING_BUDGET

    g = CustomGemini(model="gemini-3.5-flash", api_key="test-key")
    req = LlmRequest(
        model="gemini-3.5-flash",
        contents=[genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])],
        config=genai_types.GenerateContentConfig(),
    )

    captured = {}
    fake_resp = genai_types.GenerateContentResponse(candidates=[])

    async def fake_generate_content(self, **kwargs):
        captured["config"] = kwargs.get("config")
        return fake_resp

    with patch.object(
        type(g.api_client.aio.models), "generate_content", new=fake_generate_content
    ):
        async for _ in g.generate_content_async(req, stream=False):
            pass

    cfg = captured.get("config")
    assert cfg is not None, "ADK가 genai client에 config를 전달하지 않음 — forwarding 경로 변경"
    assert cfg.thinking_config is not None, "thinking_config가 genai 호출까지 전달 안 됨"
    assert cfg.thinking_config.thinking_budget == _THINKING_BUDGET


def test_clean_tools_removed():
    """R1a: additionalProperties 청소 로직은 ADK 2.3가 자체 처리(_gemini_schema_util)하므로
    죽은 코드로 제거됐다. 다시 추가되면(중복/혼란) 잡는다. self-sanitize 전제의 회귀 가드는
    test_adk_strips_additional_properties_offline(offline, CI 포함)와 smoke 도구 케이스가 담당한다."""
    from core import custom_gemini

    assert not hasattr(custom_gemini, "_clean_tools"), "_clean_tools가 되살아남 — ADK 2.3가 이미 처리(중복)"
    assert not hasattr(custom_gemini, "_clean_schema"), "_clean_schema가 되살아남"


def test_adk_strips_additional_properties_offline():
    """R1a 회귀 가드(offline, 네트워크 불필요) — _clean_tools 제거의 근거는 'ADK 2.3가
    _gemini_schema_util에서 additionalProperties를 자체 discard한다'는 전제다. 그 전제를
    CI에서 검증한다. genai Schema에는 additional_properties 필드가 실제로 존재하므로, ADK
    업그레이드가 self-sanitize를 멈추면 변환 결과에 값이 남아 이 테스트가 실패한다 →
    _clean_tools 재도입 또는 다른 대응 필요 신호. (실 API 왕복 가드는 smoke가 보완한다.)"""
    import json
    from google.adk.tools._gemini_schema_util import _to_gemini_schema

    # dict[str, str] 파라미터가 JSON 스키마에서 유발하는 additionalProperties 케이스.
    openapi = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "config": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["name", "config"],
    }
    schema = _to_gemini_schema(openapi)
    dumped = json.dumps(schema.model_dump(exclude_none=True), default=str)
    assert (
        "additional_properties" not in dumped and "additionalProperties" not in dumped
    ), "ADK 2.3가 additionalProperties를 더 이상 제거하지 않음 — Gemini 400 위험, 대응 필요"


def test_limit_thinking_sets_budget_on_config():
    """_limit_thinking이 thinking_config 미지정 시 예산을 주입한다 (호출 가능 + 동작 가드)."""
    from core import custom_gemini

    cfg: dict = {}
    custom_gemini._limit_thinking(cfg)
    assert "thinking_config" in cfg
