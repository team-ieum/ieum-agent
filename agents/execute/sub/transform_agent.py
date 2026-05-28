from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from tools.utils import json_parse, text_extract, date_format

_INSTRUCTION = (
    "데이터 가공(Transform), JSON 파싱, 정규식 기반 텍스트 추출, 날짜 포맷팅 및 마크다운 보고서 생성을 전담하는 전문 에이전트다. "
    "주어진 대량의 로우 데이터(JSON 목록 등)를 논리적으로 분류하고 요약하여, 일관되고 구조화된 문서(예: 주간 PR 요약 보고서)를 만드는 역할을 수행한다. "
    "불필요한 인사말이나 서론 없이 요구된 데이터 결과나 정제된 마크다운 결과물만 즉각적으로 반환해야 한다."
)

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
