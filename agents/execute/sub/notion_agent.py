import contextlib
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

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

    mcp = MCPToolset(
        connection_params=SseConnectionParams(
            url="https://mcp.notion.com/sse",
            headers={"Authorization": f"Bearer {notion_oauth_token}"},
        )
    )
    tools = await stack.enter_async_context(mcp)
    agent = LlmAgent(
        name="notion_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=tools
    )
    return agent, [mcp]
