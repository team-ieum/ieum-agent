import json

from mcp import ClientSession
from mcp.client.sse import sse_client


async def call_mcp_tool(server_url: str, tool_name: str, arguments: dict) -> str:
    """
    MCP 서버의 Tool을 호출합니다.

    Args:
        server_url: MCP 서버 URL (SSE 방식)
        tool_name: 호출할 Tool 이름
        arguments: Tool 파라미터 (dict)

    Returns:
        Tool 실행 결과 문자열
    """
    try:
        async with sse_client(url=server_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name=tool_name, arguments=arguments)

                if result.isError:
                    error_parts = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            error_parts.append(content.text)
                        else:
                            error_parts.append(str(content))
                    return f"MCP Tool 실행 실패: {' '.join(error_parts)}"

                parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        parts.append(content.text)
                    elif hasattr(content, "data"):
                        parts.append(json.dumps(content.data, ensure_ascii=False))
                    else:
                        parts.append(str(content))

                return "\n".join(parts) if parts else "MCP Tool 실행 완료 (결과 없음)"
    except Exception as e:
        return f"MCP Tool 호출 실패: {str(e)}"
