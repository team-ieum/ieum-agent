from typing import Optional

from google.adk.plugins.base_plugin import BasePlugin
from opentelemetry import trace

from core.telemetry import _span_cost_usd


class UsageTrackingPlugin(BasePlugin):
    """LLM 토큰 사용량을 ADK 플러그인 콜백에서 집계한다.

    ADK 2.3의 `AgentTool.run_async`는 서브에이전트를 위한 sub-Runner를 자체 생성하고
    nested 이벤트를 내부에서만 순회한다(state_delta/content/grounding_metadata만 부모로
    전파). 따라서 이벤트 루프(`runner.run_async`)에서 `event.usage_metadata`를 직접
    합산하면 AgentTool로 감싼 서브에이전트의 LLM 호출 토큰이 누락된다.

    플러그인은 `Runner(plugins=[...])`로 전달되면 `AgentTool`이 만드는 sub-Runner에도
    `include_plugins=True`(기본값) 하에 그대로 전파되므로, 부모 LLM 호출과 모든 nested
    LLM 호출을 정확히 1회씩 집계할 수 있다.

    cost는 여기서 계산해 활성(쓰기가능) span에 부착한다. OTel `SpanProcessor.on_end`는
    ReadableSpan을 받아 `set_attribute`가 없어 무력화되므로(span processor는 종료된 span을
    변경 불가), 활성 span이 살아있는 이 콜백에서 처리한다.
    """

    def __init__(self, name: str = "usage_tracking", model: str | None = None):
        super().__init__(name=name)
        self.model = model           # litellm cost 계산용 모델명(예: "anthropic/claude-sonnet-4-5")
        self.total_input = 0
        self.total_output = 0
        self.total_count = 0
        self.total_cost_usd = 0.0

    async def after_model_callback(self, *, callback_context, llm_response) -> Optional["LlmResponse"]:
        um = getattr(llm_response, "usage_metadata", None)
        if um:
            prompt = um.prompt_token_count or 0
            completion = um.candidates_token_count or 0
            self.total_input += prompt
            self.total_output += completion
            self.total_count += um.total_token_count or 0
            # cost: 모델명이 있을 때만. 계산 실패는 None(무시). 관측이 실행을 깨지 않는다.
            if self.model:
                cost = _span_cost_usd(self.model, prompt, completion)
                if cost is not None:
                    self.total_cost_usd += cost
                    try:
                        span = trace.get_current_span()
                        if span and span.is_recording():
                            span.set_attribute("ieum.cost_usd", float(cost))
                    except Exception:
                        pass  # 관측이 실행을 깨지 않는다
        return None
