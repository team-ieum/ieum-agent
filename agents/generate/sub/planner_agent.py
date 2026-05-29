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
6. 각 AI 노드에는 `tools` 필드에 해당 노드가 사용할 도구 키를 명시한다.
   - 도구 키는 아래 '참고 설계 규칙'의 도구 목록에 있는 정확한 문자열을 그대로 사용한다.
     (Notion·Google 등 빌트인 도구는 `builtin:` 프리픽스 포함, Slack/Discord/Gmail은 프리픽스 없는 키)
   - AI 노드가 아니거나(TRIGGER/HTTP/CONDITION/TRANSFORM) 도구가 필요 없으면 `tools`는 빈 리스트([])로 둔다.
   - 한 AI 노드에는 동일 목적의 도구만 넣는다. 서로 다른 서비스 도구를 한 노드에 섞지 않는다.
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
