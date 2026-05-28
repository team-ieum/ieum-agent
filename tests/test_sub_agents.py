import contextlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from google.adk.agents import LlmAgent


# ---------- WebAgent ----------

@pytest.mark.asyncio
async def test_web_agent_returns_lm_agent_and_empty_list():
    from agents.execute.sub.web_agent import build_web_agent
    agent, mcps = await build_web_agent("gemini-2.5-flash")
    assert isinstance(agent, LlmAgent)
    assert agent.name == "web_agent"
    assert mcps == []


@pytest.mark.asyncio
async def test_web_agent_has_web_search_and_http_fetch_tools():
    """WebAgent는 web_search와 http_fetch 도구를 보유한다."""
    from agents.execute.sub.web_agent import build_web_agent
    agent, _ = await build_web_agent("gemini-2.5-flash")
    tool_names = [getattr(t, "_func", getattr(t, "func", None)).__name__ for t in agent.tools]
    assert "web_search" in tool_names
    assert "http_fetch" in tool_names


# ---------- NotionAgent ----------

@pytest.mark.asyncio
async def test_notion_agent_no_token_returns_empty_tools():
    """토큰 없으면 빈 tools와 '사용할 수 없다' instruction을 반환한다."""
    from agents.execute.sub.notion_agent import build_notion_agent
    async with contextlib.AsyncExitStack() as stack:
        agent, mcps = await build_notion_agent("gemini-2.5-flash", None, stack)
    assert agent.tools == [] or agent.tools is None
    assert mcps == []
    assert "사용할 수 없다" in agent.instruction


@pytest.mark.asyncio
async def test_notion_agent_with_token_enters_mcp_toolset():
    """토큰 있으면 MCPToolset이 stack에 등록된다."""
    from agents.execute.sub.notion_agent import build_notion_agent
    mock_tools = [MagicMock()]

    with patch("agents.execute.sub.notion_agent.MCPToolset") as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_mcp.get_tools.return_value = mock_tools
        mock_mcp_cls.return_value = mock_mcp

        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=mock_tools)):
                agent, mcps = await build_notion_agent("gemini-2.5-flash", "oauth-token", stack)

    assert agent.name == "notion_agent"
    assert len(mcps) == 1
    assert agent.tools == mock_tools


@pytest.mark.asyncio
async def test_notion_agent_uses_correct_mcp_url():
    """Notion MCP 서버 URL이 mcp.notion.com/sse 이다."""
    from agents.execute.sub.notion_agent import build_notion_agent

    captured = {}

    with patch("agents.execute.sub.notion_agent.MCPToolset") as mock_mcp_cls:
        mock_mcp_cls.side_effect = lambda connection_params: (
            captured.update({"params": connection_params}) or MagicMock()
        )

        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=[])):
                await build_notion_agent("gemini-2.5-flash", "oauth-token", stack)

    assert "mcp.notion.com/sse" in captured["params"].url


# ---------- GitHubAgent ----------

@pytest.mark.asyncio
async def test_github_agent_no_pat_returns_empty_tools():
    """PAT 없으면 빈 tools와 '사용할 수 없다' instruction을 반환한다."""
    from agents.execute.sub.github_agent import build_github_agent
    async with contextlib.AsyncExitStack() as stack:
        agent, mcps = await build_github_agent("gemini-2.5-flash", None, stack)
    assert agent.tools == [] or agent.tools is None
    assert mcps == []
    assert "사용할 수 없다" in agent.instruction


@pytest.mark.asyncio
async def test_github_agent_uses_correct_mcp_url():
    """GitHub MCP 서버 URL이 api.githubcopilot.com/mcp/ 이다."""
    from agents.execute.sub.github_agent import build_github_agent

    captured = {}

    with patch("agents.execute.sub.github_agent.MCPToolset") as mock_mcp_cls:
        mock_mcp_cls.side_effect = lambda connection_params: (
            captured.update({"params": connection_params}) or MagicMock()
        )

        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=[])):
                await build_github_agent("gemini-2.5-flash", "github-pat-123", stack)

    assert "api.githubcopilot.com/mcp/" in captured["params"].url


@pytest.mark.asyncio
async def test_github_agent_includes_toolsets_header():
    """X-MCP-Toolsets 헤더가 포함된다."""
    from agents.execute.sub.github_agent import build_github_agent

    captured = {}

    with patch("agents.execute.sub.github_agent.MCPToolset") as mock_mcp_cls:
        mock_mcp_cls.side_effect = lambda connection_params: (
            captured.update({"params": connection_params}) or MagicMock()
        )

        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=[])):
                await build_github_agent("gemini-2.5-flash", "github-pat-123", stack)

    assert "X-MCP-Toolsets" in captured["params"].headers


# ---------- GoogleAgent ----------

@pytest.mark.asyncio
async def test_google_agent_no_token_returns_empty_tools():
    from agents.execute.sub.google_agent import build_google_agent
    async with contextlib.AsyncExitStack() as stack:
        agent, mcps = await build_google_agent("gemini-2.5-flash", None, stack)
    assert agent.tools == [] or agent.tools is None
    assert mcps == []


@pytest.mark.asyncio
async def test_google_agent_creates_three_mcp_toolsets():
    """Gmail, Drive, Calendar 3개의 MCPToolset을 생성한다."""
    from agents.execute.sub.google_agent import build_google_agent

    mcp_call_count = {"count": 0}

    def _count_mcp(connection_params):
        mcp_call_count["count"] += 1
        return MagicMock()

    with patch("agents.execute.sub.google_agent.MCPToolset", side_effect=_count_mcp), \
         patch("agents.execute.sub.google_agent.StreamableHTTPConnectionParams", MagicMock):
        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=[])):
                agent, mcps = await build_google_agent("gemini-2.5-flash", "google-token", stack)

    assert mcp_call_count["count"] == 3
    assert len(mcps) == 3


# ---------- CommAgent ----------

@pytest.mark.asyncio
async def test_comm_agent_has_slack_and_discord():
    """CommAgent는 slack과 discord 도구만 보유한다."""
    from agents.execute.sub.communication_agent import build_communication_agent
    agent, mcps = await build_communication_agent("gemini-2.5-flash")
    tool_names = [getattr(t, "_func", getattr(t, "func", None)).__name__ for t in agent.tools]
    assert "send_slack_message" in tool_names
    assert "send_discord_webhook" in tool_names
    assert mcps == []


@pytest.mark.asyncio
async def test_comm_agent_does_not_have_gmail():
    """CommAgent는 gmail 도구를 보유하지 않는다 (GoogleAgent로 이전됨)."""
    from agents.execute.sub.communication_agent import build_communication_agent
    agent, _ = await build_communication_agent("gemini-2.5-flash")
    tool_names = [getattr(t, "_func", getattr(t, "func", None)).__name__ for t in agent.tools]
    assert "send_gmail" not in tool_names


# ---------- McpAgent ----------

@pytest.mark.asyncio
async def test_mcp_agent_no_configs_returns_empty_tools():
    from agents.execute.sub.mcp_agent import build_mcp_agent
    async with contextlib.AsyncExitStack() as stack:
        agent, mcps = await build_mcp_agent("gemini-2.5-flash", [], stack)
    assert agent.tools == [] or agent.tools is None
    assert mcps == []
    assert "연결된 커스텀 MCP 서버가 없다" in agent.instruction


@pytest.mark.asyncio
async def test_mcp_agent_creates_toolset_per_server():
    """mcp_server_configs 수만큼 MCPToolset을 생성한다."""
    from agents.execute.sub.mcp_agent import build_mcp_agent

    configs = [
        {"server_url": "https://mcp.server1.com", "headers": {}},
        {"server_url": "https://mcp.server2.com", "headers": {"X-Key": "val"}},
    ]
    call_count = {"n": 0}

    def _count(connection_params):
        call_count["n"] += 1
        return MagicMock()

    with patch("agents.execute.sub.mcp_agent.MCPToolset", side_effect=_count):
        async with contextlib.AsyncExitStack() as stack:
            with patch.object(stack, "enter_async_context", new=AsyncMock(return_value=[])):
                agent, mcps = await build_mcp_agent("gemini-2.5-flash", configs, stack)

    assert call_count["n"] == 2
    assert len(mcps) == 2
