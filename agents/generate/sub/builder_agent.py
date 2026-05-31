from google.adk.agents import LlmAgent
from core.workflow_generator import _SYSTEM_PROMPT
from core.skill_loader import load_design_rules, format_mcp_catalog

_BUILDER_INSTRUCTION = _SYSTEM_PROMPT + """

## 추가 지시

당신은 PlannerAgent가 제공한 워크플로우 계획을 받아 실제 JSON을 생성한다.
계획에 명시된 노드 수, 타입, 역할, 연결 순서를 반드시 준수한다.
계획에 없는 노드를 임의로 추가하거나 제거하지 않는다.
각 AI 노드의 계획에 `tools` 목록이 명시되어 있으면, 그 도구 키를 그대로 노드 config의
tools에 `{"name": "<도구키>"}` 형식으로 옮긴다. 도구 키를 변형하거나 프리픽스를 임의로 추가/제거하지 않는다.
단, 계획의 도구 키가 `mcp:<catalogId>` 형식이면 노드 config의 tools에는
`{"name": "mcp", "config": {"catalogId": "<catalogId>"}}` 형식으로 변환하여 넣는다.
"""


def build_builder_agent(model: str, prompt: str, provider: str,
                        available_mcp_servers: list | None = None) -> LlmAgent:
    """BuilderAgent 빌드. 사용자 프롬프트 기반 동적 레퍼런스 및 provider 규칙 주입."""
    design_rules = load_design_rules(prompt)
    mcp_section = format_mcp_catalog(available_mcp_servers)
    instruction = (
        f"{_BUILDER_INSTRUCTION}\n\n"
        f"## 요청 프로바이더 규칙\n"
        f"- 모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다.\n\n"
        + (f"{mcp_section}\n" if mcp_section else "")
        + f"## 참고 설계 규칙 (스킬 레퍼런)\n{design_rules}"
    )
    return LlmAgent(
        name="builder_agent",
        model=model,
        instruction=instruction,
    )
