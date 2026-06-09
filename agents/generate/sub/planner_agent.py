from google.adk.agents import LlmAgent
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.skill_loader import format_mcp_catalog
from core.template_registry import slot_catalog_text

_PLANNER_INSTRUCTION = """
당신은 워크플로우 구조 계획 전문가다.
사용자의 자연어 요청을 분석하여, 아래 '노드 템플릿 카탈로그'에서 필요한 노드를 templateId로 선택하고
순서와 연결(edges)을 계획한다. 노드의 타입·도구·고정 설정은 템플릿이 결정하므로 당신은 templateId만 고른다.

## 규칙

1. 각 노드는 카탈로그에 존재하는 templateId 중 하나를 정확히 선택한다(없는 id를 만들지 않는다).
2. 워크플로우는 반드시 TRIGGER 템플릿(trigger.manual / trigger.schedule / trigger.webhook)으로 시작한다.
   nodes의 첫 번째 요소는 TRIGGER 템플릿이어야 한다.
3. Notion·Google·Slack·Discord·GitHub·Gmail 등 외부 서비스 연동에는 http 템플릿을 절대 쓰지 않고
   해당 서비스의 ai.* 템플릿을 선택한다. (http 템플릿은 전용 도구가 없는 임의 REST 호출에만)
4. 서로 다른 외부 서비스 작업은 반드시 별도 노드로 분리한다.
5. 분기가 필요하면 condition 템플릿을 적절히 배치한다.
6. MCP 의사 템플릿(ai.mcp)은 아래 '사용 가능한 MCP 서버'가 제공된 경우에만 선택할 수 있다.
7. 출력은 WorkflowPlanSchema에 부합하는 JSON Plan만 작성한다.
   각 노드는 {id, templateId, role, description} 형식이며, role/description에 노드가 할 일을 구체적으로 적는다.
"""


def build_planner_agent(model: str, prompt: str, provider: str,
                        available_mcp_servers: list | None = None) -> LlmAgent:
    """PlannerAgent 빌드. 노드 템플릿 카탈로그 + provider/MCP 규칙 주입."""
    catalog = slot_catalog_text()
    mcp_section = format_mcp_catalog(available_mcp_servers)
    instruction = (
        f"{_PLANNER_INSTRUCTION}\n\n"
        f"## 요청 프로바이더 규칙\n"
        f"- 모든 AI 노드의 llmProvider는 시스템이 \"{provider.upper()}\"로 자동 설정한다(계획에 명시 불필요).\n\n"
        + (f"{mcp_section}\n" if mcp_section else "")
        + f"## 노드 템플릿 카탈로그 (이 templateId 중에서만 선택)\n{catalog}"
    )
    return LlmAgent(
        name="planner_agent",
        model=model,
        instruction=instruction,
        output_schema=WorkflowPlanSchema,
    )
