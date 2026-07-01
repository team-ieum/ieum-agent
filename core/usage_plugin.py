from typing import Optional

from google.adk.plugins.base_plugin import BasePlugin


class UsageTrackingPlugin(BasePlugin):
    """LLM 토큰 사용량을 ADK 플러그인 콜백에서 집계한다.

    ADK 2.3의 `AgentTool.run_async`는 서브에이전트를 위한 sub-Runner를 자체 생성하고
    nested 이벤트를 내부에서만 순회한다(state_delta/content/grounding_metadata만 부모로
    전파). 따라서 이벤트 루프(`runner.run_async`)에서 `event.usage_metadata`를 직접
    합산하면 AgentTool로 감싼 서브에이전트의 LLM 호출 토큰이 누락된다.

    플러그인은 `Runner(plugins=[...])`로 전달되면 `AgentTool`이 만드는 sub-Runner에도
    `include_plugins=True`(기본값) 하에 그대로 전파되므로, 부모 LLM 호출과 모든 nested
    LLM 호출을 정확히 1회씩 집계할 수 있다.
    """

    def __init__(self, name: str = "usage_tracking"):
        super().__init__(name=name)
        self.total_input = 0
        self.total_output = 0
        self.total_count = 0

    async def after_model_callback(self, *, callback_context, llm_response) -> Optional["LlmResponse"]:
        um = getattr(llm_response, "usage_metadata", None)
        if um:
            self.total_input += um.prompt_token_count or 0
            self.total_output += um.candidates_token_count or 0
            self.total_count += um.total_token_count or 0
        return None
