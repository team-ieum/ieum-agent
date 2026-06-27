from functools import cached_property
from typing import Optional, Any
from google.genai import Client
from google.genai import types
from google.adk.models.google_llm import Gemini

# NOTE(R1a): 과거 _clean_schema/_clean_tools로 도구 스키마의 additionalProperties를
# 직접 제거했으나(genai/Gemini가 거부하므로), ADK 2.3은 google/adk/tools/
# _gemini_schema_util.py에서 genai 호출 전에 additional_properties를 자체 discard한다.
# 따라서 genai 호출 시점엔 청소할 게 남지 않아 해당 로직은 죽은 코드 → 제거했다.
# (영구 회귀 가드는 tests/smoke_adk_upgrade.py의 additionalProperties 도구 케이스가 담당.)


# Gemini 2.5 thinking 예산(토큰). 0=완전 비활성이나 도구 호출 판단까지 생략되어 에이전트가
# 조회 도구(github_list_repos 등)를 안 부르는 부작용이 있다. 작은 값으로 추론은 유지하되
# thinking 시간을 제한해 지연을 줄인다.
_THINKING_BUDGET = 512


def _limit_thinking(config: Any):
    """thinking(사고) 예산을 작은 값으로 제한해 호출당 지연을 줄인다.
    호출자가 thinking_config를 지정하지 않은 경우에만 적용한다.
    config는 genai `GenerateContentConfig`(객체) 또는 dict 둘 다 지원한다."""
    if config is None:
        return
    tc = types.ThinkingConfig(thinking_budget=_THINKING_BUDGET)
    try:
        if isinstance(config, dict):
            config.setdefault("thinking_config", tc)
        elif hasattr(config, "thinking_config"):
            if getattr(config, "thinking_config", None) is None:
                config.thinking_config = tc
    except Exception:
        pass

class CustomGemini(Gemini):
    """API Key를 os.environ 없이 동적으로 주입받는 커스텀 Gemini 모델 객체"""
    api_key: Optional[str] = None

    @cached_property
    def api_client(self) -> Client:
        base_url = self.base_url
        kwargs: dict[str, Any] = {
            'http_options': types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
                base_url=base_url,
            )
        }
        if self.model.startswith('projects/'):
            kwargs['vertexai'] = True

        # os.environ 대신 생성자에서 전달받은 api_key 주입.
        # (R1d) 이 오버라이드의 유일한 책임은 api_key 동적 주입이다. ADK 2.3 Gemini에
        # api_key 필드가 없고 preconfigured Client 주입도 미지원(adk-python#2560)이라 유지 필수.
        if self.api_key:
            kwargs['api_key'] = self.api_key

        return Client(**kwargs)

    async def generate_content_async(self, llm_request, stream: bool = False):
        """(R1d) thinking 예산을 ADK `LlmRequest.config`에 주입한 뒤 상위 구현에 위임한다.

        과거엔 genai Client의 generate_content 4종을 몽키패치했으나, 그건 genai 내부에
        강결합돼 SDK 업그레이드 때 깨지기 쉬웠다. ADK 레벨 진입점(LlmRequest.config)에서
        처리하면 genai client 구조와 무관해진다. 스트리밍/단발 모두 이 경로를 거친다."""
        _limit_thinking(llm_request.config)
        async for response in super().generate_content_async(llm_request, stream):
            yield response
