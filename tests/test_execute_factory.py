import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from api.schemas.request import AgentNodeRequest


def _make_request(agent_type="react", tools=None, mcp_servers=None):
    return AgentNodeRequest(
        nodeId="node-1",
        renderedPrompt="테스트 요청",
        agentType=agent_type,
        tools=tools or [],
        mcp_servers=mcp_servers,
    )


def _make_final_event(text="agent response"):
    part = MagicMock()
    part.text = text
    content = MagicMock()
    content.parts = [part]
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content = content
    event.usage_metadata = None
    return event


# ---------- run_simple_agent ----------

@pytest.mark.asyncio
async def test_run_simple_agent_returns_output():
    """run_simple_agent는 LLM 응답 텍스트를 반환한다."""
    from agents.execute.factory import run_simple_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("simple response")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    with patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner), \
         patch("agents.execute.factory.InMemorySessionService", return_value=mock_ss):
        output, _, _, _ = await run_simple_agent(
            model="gemini-2.5-flash",
            request=_make_request(agent_type="simple"),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
        )

    assert output == "simple response"


@pytest.mark.asyncio
async def test_run_simple_agent_creates_agent_with_no_tools():
    """run_simple_agent는 도구 없는 LlmAgent를 생성한다."""
    from agents.execute.factory import run_simple_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    with patch("agents.execute.factory.LlmAgent") as mock_llm_cls, \
         patch("agents.execute.factory.Runner", return_value=mock_runner), \
         patch("agents.execute.factory.InMemorySessionService", return_value=mock_ss):
        await run_simple_agent(
            model="gemini-2.5-flash",
            request=_make_request(agent_type="simple"),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
        )
        _, kwargs = mock_llm_cls.call_args
        assert kwargs.get("tools", []) == []


# ---------- run_react_agent ----------

@pytest.mark.asyncio
async def test_run_react_agent_returns_output():
    """run_react_agent는 멀티 에이전트 실행 후 응답 텍스트를 반환한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("react response")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s2"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_notion_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_google_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_github_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_mcp_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner), \
         patch("agents.execute.factory.InMemorySessionService", return_value=mock_ss):
        output, _, _, _ = await run_react_agent(
            model="gemini-2.5-flash",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
        )

    assert output == "react response"


@pytest.mark.asyncio
async def test_run_react_agent_builds_all_six_sub_agents():
    """run_react_agent는 6개 sub-agent 빌드 함수를 모두 호출한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s3"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    web_mock = AsyncMock(return_value=(MagicMock(), []))
    notion_mock = AsyncMock(return_value=(MagicMock(), []))
    google_mock = AsyncMock(return_value=(MagicMock(), []))
    github_mock = AsyncMock(return_value=(MagicMock(), []))
    comm_mock = AsyncMock(return_value=(MagicMock(), []))
    mcp_mock = AsyncMock(return_value=(MagicMock(), []))

    with patch("agents.execute.factory.build_web_agent", new=web_mock), \
         patch("agents.execute.factory.build_notion_agent", new=notion_mock), \
         patch("agents.execute.factory.build_google_agent", new=google_mock), \
         patch("agents.execute.factory.build_github_agent", new=github_mock), \
         patch("agents.execute.factory.build_communication_agent", new=comm_mock), \
         patch("agents.execute.factory.build_mcp_agent", new=mcp_mock), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner), \
         patch("agents.execute.factory.InMemorySessionService", return_value=mock_ss):
        await run_react_agent(
            model="gemini-2.5-flash",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
        )

    web_mock.assert_called_once()
    notion_mock.assert_called_once()
    google_mock.assert_called_once()
    github_mock.assert_called_once()
    comm_mock.assert_called_once()
    mcp_mock.assert_called_once()


@pytest.mark.asyncio
async def test_run_react_agent_passes_tokens_to_sub_agents():
    """run_react_agent는 notion_token과 github_token을 각 sub-agent 빌드 함수에 전달한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s4"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    notion_mock = AsyncMock(return_value=(MagicMock(), []))
    github_mock = AsyncMock(return_value=(MagicMock(), []))

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_notion_agent", new=notion_mock), \
         patch("agents.execute.factory.build_google_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_github_agent", new=github_mock), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_mcp_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner), \
         patch("agents.execute.factory.InMemorySessionService", return_value=mock_ss):
        await run_react_agent(
            model="gemini-2.5-flash",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            notion_token="notion-token-value",
            github_token="github-token-value",
        )

    # notion_agent 빌드 호출 시 notion_token이 전달되었는지 검증
    call_args = notion_mock.call_args
    assert "notion-token-value" in call_args.args or call_args.kwargs.get("notion_oauth_token") == "notion-token-value"

    # github_agent 빌드 호출 시 github_token이 전달되었는지 검증
    call_args = github_mock.call_args
    assert "github-token-value" in call_args.args or call_args.kwargs.get("github_token") == "github-token-value"
