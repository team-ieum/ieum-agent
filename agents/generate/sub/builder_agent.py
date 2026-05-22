from google.adk.agents import LlmAgent
from core.workflow_generator import _SYSTEM_PROMPT

_BUILDER_INSTRUCTION = _SYSTEM_PROMPT + """

## 추가 지시

당신은 PlannerAgent가 제공한 워크플로우 계획을 받아 실제 JSON을 생성한다.
계획에 명시된 노드 수, 타입, 역할, 연결 순서를 반드시 준수한다.
계획에 없는 노드를 임의로 추가하거나 제거하지 않는다.
"""


def build_builder_agent(model: str) -> LlmAgent:
    """BuilderAgent 빌드. _SYSTEM_PROMPT를 상속하여 JSON 생성 담당."""
    return LlmAgent(
        name="builder_agent",
        model=model,
        instruction=_BUILDER_INSTRUCTION,
    )
