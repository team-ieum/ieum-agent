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
async def test_run_react_agent_flattens_behavioral_subagents_and_merges_github_rules():
    """IEUM-AI-39: 단일 ReAct 경로(크레덴셜 ≤1)에서 github·transform은 더 이상 AgentTool로 감싸지
    않고 .tools로 평탄화한다(nested LLM hop 제거). github의 PR 조회 규칙은 instruction 손실 없이
    단일 에이전트 instruction에 직접 병합되어야 한다."""
    from agents.execute.factory import run_react_agent
    from agents.execute.sub.github_agent import GITHUB_PR_RULES
    from agents.execute.sub.transform_agent import TRANSFORM_OUTPUT_RULES

    gh_raw = MagicMock(); gh_raw.name = "github_raw_tool"
    gh_agent = MagicMock(); gh_agent.tools = [gh_raw]
    tf_raw = MagicMock(); tf_raw.name = "transform_raw_tool"
    tf_agent = MagicMock(); tf_agent.tools = [tf_raw]

    captured = {}

    def _llm_agent(**kwargs):
        captured["tools"] = kwargs.get("tools", [])
        captured["instruction"] = kwargs.get("instruction", "")
        return MagicMock()

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock(); mock_runner.run_async = _fake_run_async
    mock_session = MagicMock(); mock_session.id = "s-r3"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(tools=[]), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(tf_agent, []))), \
         patch("agents.execute.factory.build_github_agent", new=AsyncMock(return_value=(gh_agent, []))), \
         patch("agents.execute.factory.LlmAgent", side_effect=_llm_agent), \
         patch("agents.execute.factory.AgentTool") as mock_agent_tool, \
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

    # nested hop 제거: AgentTool이 아예 호출되지 않아야 한다
    mock_agent_tool.assert_not_called()
    # raw 도구가 단일 에이전트 tools에 직접 평탄화되어야 한다
    tool_names = [getattr(t, "name", None) for t in captured["tools"]]
    assert "github_raw_tool" in tool_names, "github raw tool이 평탄화되지 않음"
    assert "transform_raw_tool" in tool_names, "transform raw tool이 평탄화되지 않음"
    # github PR 규칙이 단일 에이전트 instruction에 직접 병합되어야 한다
    assert GITHUB_PR_RULES in captured["instruction"], "github PR 규칙이 단일 에이전트 instruction에 병합되지 않음"
    # transform 출력 규칙도 도구 호출 없이 직접 생성하는 경우를 위해 instruction에 병합되어야 한다
    assert TRANSFORM_OUTPUT_RULES in captured["instruction"], "transform 출력 규칙이 단일 에이전트 instruction에 병합되지 않음"


@pytest.mark.asyncio
async def test_run_react_agent_github_only_credential_merges_rules_and_flattens_tools():
    """github-only 단일 크레덴셜 노드(request.tools=None + github_token만) 회귀 테스트.
    규칙 문자열(merged_at 필터, search 금지 등 핵심 토큰)이 최종 단일 에이전트 instruction에
    존재하고, raw github 도구가 평탄화되어 직접 노출되어야 한다."""
    from agents.execute.factory import run_react_agent

    gh_list_prs = MagicMock(); gh_list_prs.name = "github_list_pull_requests"
    gh_agent = MagicMock(); gh_agent.tools = [gh_list_prs]

    captured = {}

    def _llm_agent(**kwargs):
        captured["tools"] = kwargs.get("tools", [])
        captured["instruction"] = kwargs.get("instruction", "")
        return MagicMock()

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock(); mock_runner.run_async = _fake_run_async
    mock_session = MagicMock(); mock_session.id = "s-gh-only"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    mock_ss.delete_session = AsyncMock()

    req = _make_request(tools=None)

    with patch("agents.execute.factory.build_web_agent", new=AsyncMock(return_value=(MagicMock(tools=[]), []))), \
         patch("agents.execute.factory.build_transform_agent", new=AsyncMock(return_value=(MagicMock(tools=[]), []))), \
         patch("agents.execute.factory.build_github_agent", new=AsyncMock(return_value=(gh_agent, []))), \
         patch("agents.execute.factory.LlmAgent", side_effect=_llm_agent), \
         patch("agents.execute.factory.AgentTool") as mock_agent_tool, \
         patch("agents.execute.factory.Runner", return_value=mock_runner):
        await run_react_agent(
            model="gemini-2.5-flash",
            provider="GEMINI",
            request=req,
            api_key="test-key",
            env_key=None,
            user_id="u1",
            github_token="gh-token-only",
            session_service=mock_ss,
        )

    mock_agent_tool.assert_not_called()
    tool_names = [getattr(t, "name", None) for t in captured["tools"]]
    assert "github_list_pull_requests" in tool_names, "raw github 도구가 평탄화되지 않음"
    instr = captured["instruction"]
    assert "merged_at" in instr, "merged_at 필터 규칙이 instruction에 없음"
    assert "search" in instr, "검색 도구 금지 규칙이 instruction에 없음"
    # systemMessage가 없으면 빈 '## 사용자 지시' 헤더가 남지 않아야 한다
    assert "## 사용자 지시" not in instr, "systemMessage 없는데 빈 사용자 지시 헤더가 남음"


# ---------- UsageTrackingPlugin model wiring (cost 관측 커버리지, IEUM-AI-46) ----------
# 지금까지는 UsageTrackingPlugin(model=cost_model_name(...))가 실제로 올바른 model을
# 넘기는지 직접 검증하는 테스트가 없었다(간접 커버만 존재). factory.py의 3개 생성 지점
# (run_simple_agent / run_react_agent 명시 도구 분기 / run_react_agent 멀티 에이전트 분기)을
# 각각 직접 검증한다.

@pytest.mark.asyncio
async def test_run_simple_agent_passes_cost_model_to_usage_plugin():
    """run_simple_agent가 UsageTrackingPlugin에 cost_model_name(provider, model, ...) 결과를 넘긴다."""
    from agents.execute.factory import run_simple_agent
    from core.model_factory import cost_model_name

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    with patch("agents.execute.factory.LlmAgent"), \
         patch("agents.execute.factory.Runner", return_value=mock_runner) as mock_runner_cls:
        await run_simple_agent(
            model="claude-sonnet-4-5",
            provider="CLAUDE",
            request=_make_request(agent_type="simple"),
            api_key="sk-test",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    _, kwargs = mock_runner_cls.call_args
    usage = kwargs["plugins"][0]
    assert usage.model == cost_model_name("CLAUDE", "claude-sonnet-4-5", "sk-test", None)


@pytest.mark.asyncio
async def test_run_react_agent_explicit_tool_node_passes_cost_model_to_usage_plugin():
    """run_react_agent의 명시 도구(single_agent) 분기가 올바른 cost model을 넘긴다."""
    from agents.execute.factory import run_react_agent
    from core.model_factory import cost_model_name

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
         patch("agents.execute.factory.Runner", return_value=mock_runner) as mock_runner_cls:
        await run_react_agent(
            model="claude-sonnet-4-5",
            provider="CLAUDE",
            request=req,
            api_key="sk-test",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    _, kwargs = mock_runner_cls.call_args
    usage = kwargs["plugins"][0]
    assert usage.model == cost_model_name("CLAUDE", "claude-sonnet-4-5", "sk-test", None)


@pytest.mark.asyncio
async def test_run_react_agent_multi_agent_passes_cost_model_to_usage_plugin():
    """run_react_agent의 멀티 에이전트(main_agent) 분기가 올바른 cost model을 넘긴다."""
    from agents.execute.factory import run_react_agent
    from core.model_factory import cost_model_name

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
         patch("agents.execute.factory.Runner", return_value=mock_runner) as mock_runner_cls:
        await run_react_agent(
            model="claude-sonnet-4-5",
            provider="CLAUDE",
            request=_make_request(),
            api_key="sk-test",
            env_key=None,
            user_id="user-1",
            session_service=mock_ss,
        )

    _, kwargs = mock_runner_cls.call_args
    usage = kwargs["plugins"][0]
    assert usage.model == cost_model_name("CLAUDE", "claude-sonnet-4-5", "sk-test", None)
