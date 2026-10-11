"""Claude 구조화 출력이 litellm에서 '강제 도구 호출' 없이 매핑되는지 고정한다(실호출 없음).

litellm 1.88.1은 response_format을 "JSON 도구 + tool_choice {type: tool}"로 바꿨고,
claude-sonnet-5-5·opus-5-5·fable-5-1은 강제 도구 호출을 400으로 거부했다.
1.105.0은 supports_native_structured_output 모델에 output_format을 쓴다.
litellm을 내리거나 lock이 어긋나면 여기서 깨진다 — 이 pytest는 dev 배포 게이트다.

LiteLlm이 실제로 만드는 값과 같게, ADK _to_litellm_response_format으로 response_format을 만들고
litellm.get_optional_params(공개 진입점, 내부에서 AnthropicConfig.map_openai_params)를 거친다.
원격 model_cost 없이(로컬 백업)도 같은 결과다.
"""
import litellm
import pytest
from google.adk.models.lite_llm import _to_litellm_response_format

from core.model_factory import _litellm_model_name

SCHEMA = {
    "type": "object",
    "properties": {"urgent": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["urgent", "reason"],
}

# BE ProviderRegistry CLAUDE 카탈로그(P4 이후 6종). 앞의 3종이 1.88.1에서 400 나던 모델이다.
CLAUDE_MODELS = [
    "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1",
    "claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5",
]


@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_claude_structured_output_uses_native_output_format(model):
    response_format = _to_litellm_response_format(SCHEMA, _litellm_model_name("CLAUDE", model))
    params = litellm.get_optional_params(
        model=model, custom_llm_provider="anthropic", response_format=response_format,
    )
    assert "tool_choice" not in params  # 강제 도구 호출이면 새 Claude가 400
    assert "tools" not in params        # JSON 도구 우회 경로도 아니다
    assert set(params["output_format"]["schema"]["required"]) == {"urgent", "reason"}
