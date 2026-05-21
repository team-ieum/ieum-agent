import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from main import app
from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from core.agent import run_agent

client = TestClient(app)

HEADERS = {
    "X-LLM-Provider": "CLAUDE",
    "X-LLM-Api-Key": "test-key",
    "X-User-Id": "test-user",
}

PAYLOAD = {
    "nodeId": "node-1",
    "workflowExecutionId": "exec-uuid-123",
    "renderedPrompt": "Hello",
    "agentType": "simple",
    "systemMessage": "You are a helpful assistant.",
}


def test_execute_success():
    mock_result = AgentExecutionResult(success=True, output="test result")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["output"] == "test result"


def test_execute_success_with_status():
    """성공 응답에 status 필드가 포함된다."""
    mock_result = AgentExecutionResult(success=True, status="COMPLETED", output="test result")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"


def test_execute_request_schema_agentType_default():
    """agentType 기본값이 'simple'이다."""
    from api.schemas.request import AgentNodeRequest
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.agentType == "simple"


def test_execute_request_schema_workflowExecutionId_optional():
    """workflowExecutionId는 optional이며 없어도 생성된다."""
    from api.schemas.request import AgentNodeRequest
    req = AgentNodeRequest(nodeId="node-1", renderedPrompt="test")
    assert req.workflowExecutionId is None


def test_execute_failure():
    mock_result = AgentExecutionResult(success=False, errorMessage="some error")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)):
        response = client.post("/v1/execute", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["errorMessage"] == "some error"


# ---------------------------------------------------------------------------
# run_agent — usage_metadata 처리
# ---------------------------------------------------------------------------

def _make_agent_request(agent_type="simple"):
    return AgentNodeRequest(
        nodeId="node-1",
        renderedPrompt="Hello",
        agentType=agent_type,
    )


def _make_usage_mock(prompt=100, candidates=50, total=150):
    usage = MagicMock()
    usage.prompt_token_count = prompt
    usage.candidates_token_count = candidates
    usage.total_token_count = total
    return usage


def _make_event(is_final=False, text=None, usage_metadata=None):
    event = MagicMock()
    event.is_final_response.return_value = is_final
    if is_final and text:
        part = MagicMock()
        part.text = text
        event.content.parts = [part]
    else:
        event.content = None
    event.usage_metadata = usage_metadata
    return event


async def _run_with_events(events, agent_type="simple"):
    """공통 패치 설정 후 run_agent()를 직접 호출하고 결과를 반환한다."""
    def _run_async_side_effect(*args, **kwargs):
        async def _gen():
            for e in events:
                yield e
        return _gen()

    mock_runner = MagicMock()
    mock_runner.run_async.side_effect = _run_async_side_effect

    mock_session = MagicMock()
    mock_session.id = "session-123"
    mock_ss = AsyncMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)

    with patch("core.agent.resolve_env_key", return_value=None), \
         patch("core.agent.resolve_model", return_value="gemini-2.5-flash"), \
         patch("core.agent.LlmAgent"), \
         patch("core.agent.InMemorySessionService", return_value=mock_ss), \
         patch("core.agent.Runner", return_value=mock_runner), \
         patch("core.agent.execution_logs") as mock_logs:

        mock_logs.insert_one = AsyncMock()

        return await run_agent(
            request=_make_agent_request(agent_type=agent_type),
            provider="gemini",
            api_key="test-key",
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_usage_simple_mode_last_value_wins():
    """Simple 모드: 이벤트가 2개여도 마지막 값으로 덮어쓴다 (누적 아님)."""
    usage = _make_usage_mock(prompt=100, candidates=50, total=150)

    event1 = _make_event(is_final=False, usage_metadata=usage)
    event2 = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event1, event2], agent_type="simple")

    assert result.usage is not None
    assert result.usage.promptTokens == 100  # 200이 아님
    assert result.usage.completionTokens == 50
    assert result.usage.totalTokens == 150


@pytest.mark.asyncio
async def test_usage_none_event_skipped():
    """usage_metadata=None 이벤트가 섞여도 오류 없이 처리된다."""
    usage = _make_usage_mock(prompt=80, candidates=40, total=120)

    event_no_usage = _make_event(is_final=False, usage_metadata=None)
    event_with_usage = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event_no_usage, event_with_usage], agent_type="simple")

    assert result.usage is not None
    assert result.usage.promptTokens == 80


@pytest.mark.asyncio
async def test_usage_none_prompt_token_treated_as_zero():
    """prompt_token_count=None이면 or 0 패턴으로 0으로 처리된다."""
    usage = _make_usage_mock(prompt=None, candidates=30, total=30)

    event = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event], agent_type="simple")

    assert result.usage is not None
    assert result.usage.promptTokens == 0
    assert result.usage.completionTokens == 30


@pytest.mark.asyncio
async def test_usage_all_zero_returns_none():
    """input + output 모두 0이면 usage는 None이다."""
    usage = _make_usage_mock(prompt=0, candidates=0, total=0)

    event = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event], agent_type="simple")

    assert result.usage is None


@pytest.mark.asyncio
async def test_usage_react_mode_accumulates() -> None:
    """ReAct 모드: 이벤트가 여러 개이면 토큰이 누적된다 (Simple 덮어쓰기와 반대)."""
    usage1 = _make_usage_mock(prompt=100, candidates=50, total=150)
    usage2 = _make_usage_mock(prompt=80, candidates=40, total=120)

    event1 = _make_event(is_final=False, usage_metadata=usage1)
    event2 = _make_event(is_final=True, text="결과", usage_metadata=usage2)

    result = await _run_with_events([event1, event2], agent_type="react")

    assert result.usage is not None
    assert result.usage.promptTokens == 180       # 100 + 80 (누적)
    assert result.usage.completionTokens == 90    # 50 + 40 (누적)
    assert result.usage.totalTokens == 270        # 150 + 120 (누적)


@pytest.mark.asyncio
async def test_usage_react_mode_none_event_skipped() -> None:
    """ReAct 모드: usage_metadata=None 이벤트는 0으로 처리된다."""
    usage = _make_usage_mock(prompt=60, candidates=30, total=90)

    event_no_usage = _make_event(is_final=False, usage_metadata=None)
    event_with_usage = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event_no_usage, event_with_usage], agent_type="react")

    assert result.usage is not None
    assert result.usage.promptTokens == 60
    assert result.usage.completionTokens == 30
    assert result.usage.totalTokens == 90


@pytest.mark.asyncio
async def test_usage_react_mode_total_zero_returns_none() -> None:
    """ReAct 모드: 누적 후 모두 0이면 usage=None이다."""
    usage = _make_usage_mock(prompt=0, candidates=0, total=0)
    event = _make_event(is_final=True, text="결과", usage_metadata=usage)

    result = await _run_with_events([event], agent_type="react")

    assert result.usage is None


# ---------------------------------------------------------------------------
# X-Notion-Token 헤더 전달 경로 검증
# ---------------------------------------------------------------------------

def test_execute_notion_token_forwarded_to_run_agent():
    """X-Notion-Token 헤더가 run_agent()의 notion_token keyword 인자로 전달된다."""
    mock_result = AgentExecutionResult(success=True, output="ok")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)) as mock_run:
        client.post(
            "/v1/execute",
            json=PAYLOAD,
            headers={**HEADERS, "X-Notion-Token": "secret_test_token"},
        )
    assert mock_run.call_args.kwargs.get("notion_token") == "secret_test_token"


def test_execute_no_notion_token_header_passes_none():
    """X-Notion-Token 헤더가 없으면 run_agent()에 None이 전달된다."""
    mock_result = AgentExecutionResult(success=True, output="ok")
    with patch("api.routes.execute.run_agent", new=AsyncMock(return_value=mock_result)) as mock_run:
        client.post(
            "/v1/execute",
            json=PAYLOAD,
            headers=HEADERS,  # X-Notion-Token 없음
        )
    assert mock_run.call_args.kwargs.get("notion_token") is None
