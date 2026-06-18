import contextlib
import inspect
import logging
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

from agents.base import _safe_close_mcp
from core.config import settings

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
    logger.info(
        "[notion_agent] Notion MCP(HTTP) 연결 시도 — url=%s, token_prefix=%s",
        settings.NOTION_MCP_URL, token_prefix,
    )

    # self-host한 notion-mcp-server(--transport http --enable-token-passthrough)에 연결한다.
    # 유저별 토큰은 매 요청 Notion-Token 헤더로 전달되며, 서버가 이를 Notion API 호출에 사용한다.
    params = StreamableHTTPConnectionParams(
        url=settings.NOTION_MCP_URL,
        headers={
            "Notion-Token": notion_oauth_token,
            "Notion-Version": "2022-06-28",
        },
    )

    mcp = MCPToolset(connection_params=params)
    stack.push_async_callback(_safe_close_mcp, mcp)

    res = mcp.get_tools()
    tools = await res if inspect.isawaitable(res) else res

    agent = LlmAgent(
        name="notion_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, [mcp]
