from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from tools.utils import json_parse, text_extract, date_format

_CAPABILITY = (
    "데이터 가공(Transform), JSON 파싱, 정규식 기반 텍스트 추출, 날짜 포맷팅 및 마크다운 보고서 생성을 전담하는 전문 에이전트다. "
    "주어진 대량의 로우 데이터(JSON 목록 등)를 논리적으로 분류하고 요약하여, 일관되고 구조화된 문서(예: 주간 PR 요약 보고서)를 만드는 역할을 수행한다."
)

# 출력 규칙. 도구 docstring에도 있으나, 도구를 호출하지 않고 직접 생성하는 경우(fast-path)나
# AgentTool로 감싸인 멀티에이전트 경로에서는 docstring이 모델에 도달하지 않으므로 instruction에도 유지한다.
TRANSFORM_OUTPUT_RULES = (
    " 불필요한 인사말이나 서론 없이 요구된 데이터 결과나 정제된 마크다운 결과물만 즉각적으로 반환해야 한다."
)

_INSTRUCTION = _CAPABILITY + TRANSFORM_OUTPUT_RULES

async def build_transform_agent(model: str) -> tuple[LlmAgent, list]:
    """TransformAgent 빌드. MCPToolset 없으므로 빈 리스트 반환."""
    tools = [
        FunctionTool(json_parse),
        FunctionTool(text_extract),
        FunctionTool(date_format),
    ]
    agent = LlmAgent(
        name="transform_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, []
