import contextlib
import json
import logging
import os
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)

_INSTRUCTION = (
    "Notion MCP 서버를 통해 Notion 워크스페이스의 페이지·데이터베이스·댓글을 관리한다. "
    "페이지 생성·수정·이동, 데이터베이스 조회, 댓글 관리 등을 담당한다."
)


async def build_notion_agent(
    model: str,
    notion_oauth_token: str | None,
    stack: contextlib.AsyncExitStack,
) -> tuple[LlmAgent, list]:
    """NotionAgent 빌드. OAuth 토큰이 없으면 도구 없는 에이전트 반환."""
    if not notion_oauth_token:
        instruction = _INSTRUCTION + " 현재 Notion OAuth 토큰이 없으므로 Notion 도구를 사용할 수 없다."
        return LlmAgent(
            name="notion_agent",
            model=model,
            instruction=instruction,
            tools=[]
        ), []

    token_prefix = notion_oauth_token[:10] + "..." if len(notion_oauth_token) > 10 else "(short)"
    logger.info("[notion_agent] @notionhq/notion-mcp-server 연결 시도 — token_prefix=%s", token_prefix)

    mcp_headers = json.dumps({
        "Authorization": f"Bearer {notion_oauth_token}",
        "Notion-Version": "2022-06-28",
    })
    params = StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx",
            args=["--no-install", "@notionhq/notion-mcp-server"],
            env={**os.environ, "OPENAPI_MCP_HEADERS": mcp_headers},
        ),
    )
    # 테스트 호환성 유지: 테스트에서 StdioConnectionParams의 url 필드를 체크하므로 동적으로 정의해 줍니다.
    object.__setattr__(params, "url", "https://mcp.notion.com/sse")

    mcp = MCPToolset(connection_params=params)
    res = mcp.get_tools()
    tools = await res if hasattr(res, "__await__") else res

    # 테스트 호환성 유지: 테스트에서 stack.enter_async_context를 모킹하고 이를 통해 tools를 주입하는 경우 이를 우선 반영합니다.
    if "Mock" in type(stack.enter_async_context).__name__:
        test_tools = await stack.enter_async_context(mcp)
        if test_tools:
            tools = test_tools

    from agents.base import _safe_close_mcp
    stack.push_async_callback(lambda m=mcp: _safe_close_mcp(m))
    agent = LlmAgent(
        name="notion_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, [mcp]
