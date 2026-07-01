import pytest
from unittest.mock import MagicMock

from core.usage_plugin import UsageTrackingPlugin


def _make_llm_response(prompt=None, candidates=None, total=None, usage_metadata="_default"):
    """usage_metadata를 가진(또는 None인) fake LlmResponse를 만든다."""
    response = MagicMock()
    if usage_metadata is None:
        response.usage_metadata = None
    else:
        um = MagicMock()
        um.prompt_token_count = prompt
        um.candidates_token_count = candidates
        um.total_token_count = total
        response.usage_metadata = um
    return response


@pytest.mark.asyncio
async def test_plugin_starts_at_zero():
    """플러그인 생성 직후 누적값은 0이다."""
    plugin = UsageTrackingPlugin()
    assert plugin.total_input == 0
    assert plugin.total_output == 0
    assert plugin.total_count == 0


@pytest.mark.asyncio
async def test_plugin_accumulates_single_response():
    """usage_metadata가 있는 응답 1건이 그대로 누적된다."""
    plugin = UsageTrackingPlugin()
    response = _make_llm_response(prompt=100, candidates=50, total=150)

    result = await plugin.after_model_callback(callback_context=None, llm_response=response)

    assert result is None  # 원본 응답을 그대로 사용하도록 None 반환
    assert plugin.total_input == 100
    assert plugin.total_output == 50
    assert plugin.total_count == 150


@pytest.mark.asyncio
async def test_plugin_accumulates_multiple_responses():
    """여러 번 호출되면(예: 부모 + AgentTool로 감싼 서브에이전트) 토큰이 누적된다."""
    plugin = UsageTrackingPlugin()

    await plugin.after_model_callback(
        callback_context=None,
        llm_response=_make_llm_response(prompt=100, candidates=50, total=150),
    )
    await plugin.after_model_callback(
        callback_context=None,
        llm_response=_make_llm_response(prompt=30, candidates=20, total=50),
    )

    assert plugin.total_input == 130
    assert plugin.total_output == 70
    assert plugin.total_count == 200


@pytest.mark.asyncio
async def test_plugin_ignores_none_usage_metadata():
    """usage_metadata=None인 응답은 무시되고 누적값이 변하지 않는다."""
    plugin = UsageTrackingPlugin()
    response = _make_llm_response(usage_metadata=None)

    await plugin.after_model_callback(callback_context=None, llm_response=response)

    assert plugin.total_input == 0
    assert plugin.total_output == 0
    assert plugin.total_count == 0


@pytest.mark.asyncio
async def test_plugin_treats_missing_token_counts_as_zero():
    """개별 카운트 필드가 None이면 0으로 취급한다(or 0 패턴)."""
    plugin = UsageTrackingPlugin()
    response = _make_llm_response(prompt=None, candidates=30, total=30)

    await plugin.after_model_callback(callback_context=None, llm_response=response)

    assert plugin.total_input == 0
    assert plugin.total_output == 30
    assert plugin.total_count == 30


@pytest.mark.asyncio
async def test_plugin_ignores_response_without_usage_metadata_attribute():
    """usage_metadata 속성 자체가 없는 객체도 오류 없이 무시된다."""
    plugin = UsageTrackingPlugin()
    response = object()

    await plugin.after_model_callback(callback_context=None, llm_response=response)

    assert plugin.total_input == 0
    assert plugin.total_output == 0
    assert plugin.total_count == 0
