from google.adk.agents import LlmAgent
from core.workflow_generator import _SYSTEM_PROMPT
from core.skill_loader import format_mcp_catalog
from core.template_registry import slot_catalog_text

_BUILDER_INSTRUCTION = _SYSTEM_PROMPT + """

## 추가 지시

당신은 PlannerAgent가 제공한 계획을 받아 각 노드를 {id, templateId, slots} draft로 출력한다.
- 계획에 명시된 노드 수, templateId, 연결 순서를 반드시 그대로 따른다.
  계획에 없는 노드를 추가하거나 제거하지 않는다.
- 각 노드의 templateId는 계획 값을 그대로 사용한다(변형 금지).
- slots는 해당 templateId가 정의한 슬롯만 채운다(아래 '노드 템플릿 카탈로그' 참조).
  provider 슬롯(자동주입 표기)은 작성하지 않는다.
"""


def build_builder_agent(model: str, prompt: str, provider: str,
                        available_mcp_servers: list | None = None) -> LlmAgent:
    """BuilderAgent 빌드. 노드 템플릿 카탈로그(슬롯 스펙) + provider/MCP 규칙 주입."""
    catalog = slot_catalog_text()
    mcp_section = format_mcp_catalog(available_mcp_servers)
    instruction = (
        f"{_BUILDER_INSTRUCTION}\n\n"
        f"## 요청 프로바이더 규칙\n"
        f"- 모든 AI 노드의 llmProvider는 시스템이 \"{provider.upper()}\"로 자동 주입한다(slots에 작성 금지).\n\n"
        + (f"{mcp_section}\n" if mcp_section else "")
        + f"## 노드 템플릿 카탈로그 (templateId + 채울 슬롯)\n{catalog}"
    )
    return LlmAgent(
        name="builder_agent",
        model=model,
        instruction=instruction,
    )
