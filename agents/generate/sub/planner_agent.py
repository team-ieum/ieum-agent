from google.adk.agents import LlmAgent
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.skill_loader import load_design_rules

_PLANNER_INSTRUCTION = """
당신은 워크플로우 구조 계획 전문가다.
사용자의 자연어 요청을 분석하여 필요한 노드의 타입, 역할, 순서를 계획한다.

## 규칙

1. 서로 다른 외부 서비스를 호출하는 작업은 반드시 별도 AI 노드로 분리한다.
   예: Slack 전송과 Notion 등록은 각각 다른 AI 노드로 배치한다.
2. 워크플로우는 반드시 TRIGGER 노드로 시작한다. (nodes의 첫 번째 요소는 type이 'TRIGGER'여야 함)
3. Notion, Google, Slack, Discord, GitHub 등의 연동에는 HTTP 노드를 절대 사용하지 않고,
   반드시 도구를 지닌 AI 노드를 배치하도록 구조를 짠다.
4. 분기가 필요한 경우 CONDITION 노드를 적절히 설계한다.
5. 출력 규격(WorkflowPlanSchema)에 부합하는 JSON Plan만 올바르게 작성해야 한다.
"""


def build_planner_agent(model: str, prompt: str, provider: str) -> LlmAgent:
    """PlannerAgent 빌드. 사용자 프롬프트 기반 동적 레퍼런스 및 provider 규칙 주입."""
    design_rules = load_design_rules(prompt)
    instruction = (
        f"{_PLANNER_INSTRUCTION}\n\n"
        f"## 요청 프로바이더 규칙\n"
        f"- 모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다.\n\n"
        f"## 참고 설계 규칙 (스킬 레퍼런스)\n{design_rules}"
    )
    return LlmAgent(
        name="planner_agent",
        model=model,
        instruction=instruction,
        output_schema=WorkflowPlanSchema,
    )
