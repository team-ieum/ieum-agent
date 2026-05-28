from google.adk.agents import LlmAgent
from core.workflow_generator import _SYSTEM_PROMPT
from core.skill_loader import load_design_rules

_BUILDER_INSTRUCTION = _SYSTEM_PROMPT + """

## 추가 지시

당신은 PlannerAgent가 제공한 워크플로우 계획을 받아 실제 JSON을 생성한다.
계획에 명시된 노드 수, 타입, 역할, 연결 순서를 반드시 준수한다.
계획에 없는 노드를 임의로 추가하거나 제거하지 않는다.
"""


def build_builder_agent(model: str, prompt: str, provider: str) -> LlmAgent:
    """BuilderAgent 빌드. 사용자 프롬프트 기반 동적 레퍼런스 및 provider 규칙 주입."""
    design_rules = load_design_rules(prompt)
    instruction = (
        f"{_BUILDER_INSTRUCTION}\n\n"
        f"## 요청 프로바이더 규칙\n"
        f"- 모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다.\n\n"
        f"## 참고 설계 규칙 (스킬 레퍼런)\n{design_rules}"
    )
    return LlmAgent(
        name="builder_agent",
        model=model,
        instruction=instruction,
    )
