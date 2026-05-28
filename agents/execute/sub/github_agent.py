import contextlib
import inspect
import logging
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

logger = logging.getLogger(__name__)

_INSTRUCTION = (
    "GitHub MCP 서버를 통해 리포지토리·이슈·Pull Request·GitHub Actions를 관리한다. "
    "코드 검색, 파일 조회, PR 생성·리뷰, 이슈 트래킹, 워크플로우 실행 등을 담당한다."
)


async def build_github_agent(
    model: str,
    github_token: str | None,
    stack: contextlib.AsyncExitStack,
) -> tuple[LlmAgent, list]:
    """GitHubAgent 빌드. 토큰이 없으면 도구 없는 에이전트 반환."""
    if not github_token:
        instruction = _INSTRUCTION + " 현재 GitHub 토큰이 없으므로 GitHub 도구를 사용할 수 없다."
        return LlmAgent(
            name="github_agent",
            model=model,
            instruction=instruction,
            tools=[]
        ), []

    mcp = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://api.githubcopilot.com/mcp/",
            headers={
                "Authorization": f"Bearer {github_token}",
                "X-MCP-Toolsets": "repos,issues,pull_requests,actions",
            },
        )
    )
    async def _safe_close():
        res = mcp.close()
        if inspect.isawaitable(res):
            await res
    stack.push_async_callback(_safe_close)

    res = mcp.get_tools()
    tools = await res if inspect.isawaitable(res) else res

    agent = LlmAgent(
        name="github_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, [mcp]
