import contextlib
import logging
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

logger = logging.getLogger(__name__)

_INSTRUCTION = (
    "Google Workspace MCP 서버를 통해 Gmail·Google Drive·Google Calendar를 관리한다. "
    "이메일 조회·작성, 파일 관리, 일정 생성·조회 등을 담당한다."
)

_GOOGLE_MCP_URLS = [
    "https://gmailmcp.googleapis.com/mcp/v1",
    "https://drivemcp.googleapis.com/mcp/v1",
    "https://calendarmcp.googleapis.com/mcp/v1",
]

async def build_google_agent(
    model: str,
    google_oauth_token: str | None,
    stack: contextlib.AsyncExitStack,
) -> tuple[LlmAgent, list]:
    """GoogleAgent 빌드. OAuth 토큰이 없으면 도구 없는 에이전트 반환."""
    if not google_oauth_token:
        instruction = _INSTRUCTION + " 현재 Google OAuth 토큰이 없으므로 Google 도구를 사용할 수 없다."
        return LlmAgent(
            name="google_agent",
            model=model,
            instruction=instruction,
            tools=[]
        ), []

    all_tools = []
    mcps = []
    for url in _GOOGLE_MCP_URLS:
        mcp = MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=url,
                headers={"Authorization": f"Bearer {google_oauth_token}"},
            )
        )
        tools = await mcp.get_tools()
        stack.push_async_callback(mcp.close)
        all_tools.extend(tools)
        mcps.append(mcp)

    agent = LlmAgent(
        name="google_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=all_tools
    )
    return agent, mcps
