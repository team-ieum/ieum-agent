from google.adk.agents import LlmAgent
from google.adk.tools.function_tool import FunctionTool
from tools.web_search import web_search
from tools.http_fetch import http_fetch

_INSTRUCTION = (
    "웹 검색(web_search)과 HTTP 요청(http_fetch)을 처리하는 전문 에이전트다. "
    "외부 URL 조회, 뉴스 검색, API 호출 등을 담당한다."
)

async def build_web_agent(model: str) -> tuple[LlmAgent, list]:
    """WebAgent 빌드. MCPToolset 없으므로 빈 리스트 반환."""
    tools = [FunctionTool(web_search), FunctionTool(http_fetch)]
    agent = LlmAgent(
        name="web_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, []
