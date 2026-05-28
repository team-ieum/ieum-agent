import contextlib
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams
from agents.base import _safe_close_mcp

_INSTRUCTION = (
    "사용자가 지정한 커스텀 MCP 서버의 도구를 활용해 요청된 작업을 수행한다. "
    "연결된 MCP 서버의 도구 목록을 확인하고 적절한 도구를 선택한다."
)

async def build_mcp_agent(
    model: str,
    mcp_server_configs: list[dict],
    stack: contextlib.AsyncExitStack,
) -> tuple[LlmAgent, list]:
    """McpAgent 빌드. 사용자 정의 MCP 서버 목록으로 MCPToolset 다중 연결.

        현재 SSE(SseConnectionParams) 방식만 지원한다. (MVP 범위)
        Stdio 방식이 필요한 경우 StdioServerParameters 분기를 추가한다.
        """
    if not mcp_server_configs:
        instruction = _INSTRUCTION + " 현재 연결된 커스텀 MCP 서버가 없다."
        return LlmAgent(
            name="mcp_agent",
            model=model,
            instruction=instruction,
            tools=[]
        ), []

    all_tools = []
    mcps = []
    for cfg in mcp_server_configs:
        # MVP: SSE 방식만 지원. Stdio 지원이 필요하면 command 키 유무로 분기 추가.
        mcp = MCPToolset(
            connection_params=SseConnectionParams(
                url=cfg["server_url"],
                headers=cfg.get("headers", {}),
            )
        )
        res = mcp.get_tools()
        tools = await res if hasattr(res, "__await__") else res
        stack.push_async_callback(lambda m=mcp: _safe_close_mcp(m))
        all_tools.extend(tools)
        mcps.append(mcp)

    agent = LlmAgent(
        name="mcp_agent",
        model=model,
        instruction=_INSTRUCTION,
        tools=all_tools
    )
    return agent, mcps
