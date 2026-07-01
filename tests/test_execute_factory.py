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


def _make_event_with_calls(n_calls=0, final_text="agent response"):
    """function_call 개수를 제어할 수 있는 final 이벤트."""
    event = _make_final_event(final_text)
    event.get_function_calls.return_value = [MagicMock() for _ in range(n_calls)]
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
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        output, _, _, _ = await run_simple_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(agent_type="simple"),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
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
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_simple_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(agent_type="simple"),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )
        _, kwargs = mock_llm_cls.call_args
        assert kwargs.get("tools", []) == []


@pytest.mark.asyncio
async def test_run_simple_agent_deletes_session_after_run():
    """단발성 simple 세션은 실행 종료 후 delete_session으로 폐기된다."""
    from agents.execute.factory import run_simple_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    with patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_simple_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(agent_type="simple"),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    mock_ss.delete_session.assert_called_once_with(
        app_name="ieum-agent", user_id="user-1", session_id="s1"
    )


# ---------- _assert_tool_called (도구 미호출 감지 가드) ----------

def test_assert_tool_called_raises_when_tools_set_but_no_calls():
    """도구가 명시됐는데 호출이 0건이면 ToolNotCalledError."""
    from agents.execute.factory import _assert_tool_called, ToolNotCalledError

    with pytest.raises(ToolNotCalledError):
        _assert_tool_called([{"name": "discord"}], 0)


def test_assert_tool_called_passes_when_called():
    """도구가 한 번이라도 호출되면 통과한다."""
    from agents.execute.factory import _assert_tool_called

    _assert_tool_called([{"name": "discord"}], 1)  # 예외 없어야 함


def test_assert_tool_called_passes_when_no_tools():
    """도구 미명시(sub-agent 위임 노드)는 호출 0건이어도 통과한다."""
    from agents.execute.factory import _assert_tool_called

    _assert_tool_called([], 0)
    _assert_tool_called(None, 0)


@pytest.mark.asyncio
async def test_run_react_agent_raises_when_tool_never_called():
    """tools 명시 노드가 도구를 한 번도 호출하지 않으면 ToolNotCalledError를 던진다.
    (LLM이 도구 없이 자연어로 '못 했다'고 답하는 조용한 실패 방지)"""
    from agents.execute.factory import run_react_agent, ToolNotCalledError

    async def _fake_run_async(**kwargs):
        yield _make_event_with_calls(0, final_text="웹훅 URL이 없어 발송할 수 없습니다.")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s-noncall"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    req = _make_request(tools=[{"name": "discord", "config": {}}])

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        with pytest.raises(ToolNotCalledError):
            await run_react_agent(
                model="gemini-2.5-flash",
                provider="GEMINI",
                request=req,
                api_key="test-key",
                env_key=None,
                user_id="user-1",
                session_service=mock_ss,
            )


@pytest.mark.asyncio
async def test_run_react_agent_succeeds_when_tool_called():
    """tools 명시 노드가 도구를 호출하면 정상적으로 출력을 반환한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_event_with_calls(1, final_text="발송 완료")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s-call"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    req = _make_request(tools=[{"name": "discord", "config": {}}])

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        output, _, _, _ = await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=req,
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    assert output == "발송 완료"


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
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        output, _, _, _ = await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    assert output == "react response"


@pytest.mark.asyncio
async def test_run_react_agent_deletes_session_single_path():
    """단일 ReAct 경로(토큰 ≤1, 커스텀 MCP 없음) 종료 후 세션을 폐기한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s-single"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    mock_ss.delete_session.assert_called_once_with(
        app_name="ieum-agent", user_id="user-1", session_id="s-single"
    )


@pytest.mark.asyncio
async def test_run_react_agent_deletes_session_multi_path():
    """멀티 에이전트 경로(use_single_agent=False) 종료 후 세션을 폐기한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s-multi"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_notion_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_google_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_github_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_communication_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.build_mcp_agent", new=AsyncMock(return_value=(MagicMock(), []))), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
            use_single_agent=False,
        )

    mock_ss.delete_session.assert_called_once_with(
        app_name="ieum-agent", user_id="user-1", session_id="s-multi"
    )


@pytest.mark.asyncio
async def test_run_react_agent_builds_helpers_for_toolless_node():
    """도구 없는 능력형 노드(tools=[])는 web/transform 헬퍼를 빌드한다. webhook 없으면 comm은 빌드 안 한다."""
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
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
            use_single_agent=False,
        )

    web_mock.assert_called_once()
    comm_mock.assert_not_called()  # webhook 설정 없음 → comm 미빌드
    notion_mock.assert_not_called()
    google_mock.assert_not_called()
    github_mock.assert_not_called()
    mcp_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_react_agent_builds_conditional_agents_with_tokens():
    """토큰/설정이 모두 있을 때 6개 sub-agent가 모두 빌드된다."""
    from agents.execute.factory import run_react_agent
    from api.schemas.request import McpServerConfig

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s3b"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    web_mock = AsyncMock(return_value=(MagicMock(), []))
    notion_mock = AsyncMock(return_value=(MagicMock(), []))
    google_mock = AsyncMock(return_value=(MagicMock(), []))
    github_mock = AsyncMock(return_value=(MagicMock(), []))
    comm_mock = AsyncMock(return_value=(MagicMock(), []))
    mcp_mock = AsyncMock(return_value=(MagicMock(), []))

    request_with_mcp = _make_request(
        mcp_servers=[McpServerConfig(server_url="https://mcp.example.com")]
    )

    with patch("agents.execute.factory.build_web_agent", new=web_mock), \
         patch("agents.execute.factory.build_notion_agent", new=notion_mock), \
         patch("agents.execute.factory.build_google_agent", new=google_mock), \
         patch("agents.execute.factory.build_github_agent", new=github_mock), \
         patch("agents.execute.factory.build_communication_agent", new=comm_mock), \
         patch("agents.execute.factory.build_mcp_agent", new=mcp_mock), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=request_with_mcp,
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            notion_token="notion-token",
            google_access_token="google-token",
            github_token="github-token",
            session_service=mock_ss,
            use_single_agent=False,
        )

    web_mock.assert_called_once()
    comm_mock.assert_not_called()  # webhook 설정 없음 → comm 미빌드
    notion_mock.assert_called_once()
    google_mock.assert_called_once()
    github_mock.assert_called_once()
    mcp_mock.assert_called_once()


@pytest.mark.asyncio
async def test_run_react_agent_skips_helpers_for_explicit_tool_node():
    """명시 도구가 있는 노드는 web/transform 헬퍼를 빌드하지 않고, webhook 도구면 comm만 빌드한다."""
    from agents.execute.factory import run_react_agent

    async def _fake_run_async(**kwargs):
        yield _make_event_with_calls(1, final_text="발송 완료")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s3c"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    web_mock = AsyncMock(return_value=(MagicMock(), []))
    transform_mock = AsyncMock(return_value=(MagicMock(), []))
    comm_mock = AsyncMock(return_value=(MagicMock(), []))

    req = _make_request(tools=[{"name": "discord", "config": {"webhook_url": "https://x"}}])

    with patch("agents.execute.factory.build_web_agent", new=web_mock), \
         patch("agents.execute.factory.build_transform_agent", new=transform_mock), \
         patch("agents.execute.factory.build_communication_agent", new=comm_mock), \
         patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.AgentTool", side_effect=lambda agent: MagicMock()), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=req,
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
            use_single_agent=False,
        )

    comm_mock.assert_called_once()       # webhook 도구 있음 → comm 빌드
    web_mock.assert_not_called()         # 명시 도구 노드 → web 헬퍼 미빌드
    transform_mock.assert_not_called()   # 명시 도구 노드 → transform 헬퍼 미빌드


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
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),
            api_key="test-key",
            env_key=None,
            user_id="user-1",
            notion_token="notion-token-value",
            github_token="github-token-value",
            session_service=mock_ss,
            use_single_agent=False,
        )

    # notion_agent 빌드 호출 시 notion_token이 전달되었는지 검증
    call_args = notion_mock.call_args
    assert "notion-token-value" in call_args.args or call_args.kwargs.get("notion_oauth_token") == "notion-token-value"

    # github_agent 빌드 호출 시 github_token이 전달되었는지 검증
    call_args = github_mock.call_args
    assert "github-token-value" in call_args.args or call_args.kwargs.get("github_token") == "github-token-value"


@pytest.mark.asyncio
async def test_run_react_agent_wraps_behavioral_subagents_as_agent_tool():
    """R2 회귀: 단일 ReAct 경로(크레덴셜 ≤1)에서 행동규칙 보유 서브(github·transform)는
    AgentTool로 감싸 instruction을 보존해야 한다. .tools만 평탄화하면 github의 PR 조회 규칙·
    transform의 출력 규칙이 단일 크레덴셜 노드에서 증발한다(버그)."""
    from agents.execute.factory import run_react_agent

    gh_raw = MagicMock(); gh_raw.name = "github_raw_tool"
    gh_agent = MagicMock(); gh_agent.tools = [gh_raw]
    tf_raw = MagicMock(); tf_raw.name = "transform_raw_tool"
    tf_agent = MagicMock(); tf_agent.tools = [tf_raw]

    wrapped = []

    def _agent_tool(agent):
        wrapped.append(agent)
        m = MagicMock(); m.name = f"agenttool:{id(agent)}"
        return m

    captured = {}

    def _llm_agent(**kwargs):
        captured["tools"] = kwargs.get("tools", [])
        return MagicMock()

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock(); mock_runner.run_async = _fake_run_async
    mock_session = MagicMock(); mock_session.id = "s-r2"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(tools=[]), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(tf_agent, []))), \
         patch("agents.execute.factory.build_github_agent", new=AsyncMock(return_value=(gh_agent, []))), \
         patch("agents.execute.factory.LlmAgent", side_effect=_llm_agent), \
         patch("agents.execute.factory.AgentTool", side_effect=_agent_tool), \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=_make_request(),  # tools 없음 → 능력형 노드(web/transform 마운트) + github 단일 크레덴셜
            api_key="test-key",
            env_key=None,
            user_id="u1",
            github_token="gh-token",
            session_service=mock_ss,
        )

    # github·transform이 AgentTool로 감싸졌는가 (instruction 보존)
    assert gh_agent in wrapped, "github_agent가 AgentTool로 안 감싸짐 — PR 규칙 증발"
    assert tf_agent in wrapped, "transform_agent가 AgentTool로 안 감싸짐 — 출력 규칙 증발"
    # raw 도구가 단일 에이전트 tools에 직접 평탄화되면 안 됨(AgentTool 뒤에 있어야 instruction 적용)
    tool_names = [getattr(t, "name", None) for t in captured["tools"]]
    assert "github_raw_tool" not in tool_names, "github raw tool 평탄화 — instruction 우회됨"
    assert "transform_raw_tool" not in tool_names, "transform raw tool 평탄화 — instruction 우회됨"
