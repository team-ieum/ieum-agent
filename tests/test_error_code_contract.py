"""
tests/test_error_code_contract.py

IEUM-AI-51 회귀 테스트. 실패 응답의 errorCode(ErrorCode enum 이름)가 BE FailureClassifier
계약과 1:1로 맞는지 검증한다. errorCode가 비면 BE는 실패를 UNKNOWN으로 보고 재시도하지 않는다.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.schemas.request import AgentNodeRequest
from common.error_code import ErrorCode
from common.exception import is_rate_limit_error
from core.agent import run_agent
from main import app


# BE FailureClassifier가 기대하는 이름들. 이름이 바뀌면 재시도 분류가 조용히 깨진다.
BE_CONTRACT_NAMES = [
    "RATE_LIMITED",
    "AGENT_TIMEOUT",
    "AGENT_TOOL_NOT_CALLED",
    "MISSING_CREDENTIAL",
    "AGENT_EXECUTION_FAILED",
]


def test_be_계약_errorcode_이름이_모두_존재한다():
    for name in BE_CONTRACT_NAMES:
        assert ErrorCode[name].name == name


# ---------------------------------------------------------------------------
# rate limit 예외 판별 (핵심)
# ---------------------------------------------------------------------------

class _LiteLlmRateLimit(Exception):
    """litellm/openai/anthropic 계열: .status_code = 429."""
    def __init__(self):
        self.status_code = 429
        super().__init__("RateLimitError: quota exceeded")


class _GenaiResourceExhausted(Exception):
    """google-genai 계열: .code = 429."""
    def __init__(self):
        self.code = 429
        super().__init__("429 RESOURCE_EXHAUSTED")


def test_is_rate_limit_error_litellm_status_code():
    assert is_rate_limit_error(_LiteLlmRateLimit()) is True


def test_is_rate_limit_error_체인으로_감싸인_429():
    """ADK/MCP 정리 과정에서 다른 예외로 감싸여도 429를 놓치면 안 된다."""
    try:
        try:
            raise _GenaiResourceExhausted()
        except Exception:
            raise RuntimeError("Attempted to exit cancel scope in a different task")
    except RuntimeError as wrapped:
        assert is_rate_limit_error(wrapped) is True


def test_is_rate_limit_error_exception_group():
    group = ExceptionGroup("task group failed", [ValueError("무관"), _LiteLlmRateLimit()])
    assert is_rate_limit_error(group) is True


def test_is_rate_limit_error_타입명만으로도_판별():
    class RateLimitError(Exception):
        pass

    assert is_rate_limit_error(RateLimitError("Rate limit reached")) is True


def test_is_rate_limit_error_무관한_예외는_False():
    assert is_rate_limit_error(RuntimeError("adk crashed")) is False
    assert is_rate_limit_error(ExceptionGroup("g", [ValueError("무관")])) is False


# ---------------------------------------------------------------------------
# run_agent() 실패 경로별 errorCode
# ---------------------------------------------------------------------------

async def _run_with_side_effect(side_effect) -> "object":
    request = AgentNodeRequest(nodeId="node-1", renderedPrompt="Hello", tools=[])
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=side_effect)

    with (
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        return await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service,
        )


@pytest.mark.asyncio
async def test_run_agent_litellm_rate_limit은_RATE_LIMITED():
    result = await _run_with_side_effect(_LiteLlmRateLimit())
    assert result.errorCode == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_run_agent_감싸인_rate_limit도_RATE_LIMITED():
    """provider 429가 ExceptionGroup에 감싸여 올라와도 재시도 가능하게 분류돼야 한다."""
    group = ExceptionGroup("adk run failed", [_GenaiResourceExhausted()])
    result = await _run_with_side_effect(group)
    assert result.errorCode == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_run_agent_일반_예외는_AGENT_EXECUTION_FAILED():
    result = await _run_with_side_effect(RuntimeError("adk crashed"))
    assert result.errorCode == "AGENT_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_run_agent_타임아웃은_AGENT_TIMEOUT():
    result = await _run_with_side_effect(asyncio.TimeoutError())
    assert result.errorCode == "AGENT_TIMEOUT"


@pytest.mark.asyncio
async def test_run_agent_도구_미호출은_AGENT_TOOL_NOT_CALLED():
    from agents.execute.factory import ToolNotCalledError

    result = await _run_with_side_effect(ToolNotCalledError("도구 미호출"))
    assert result.errorCode == "AGENT_TOOL_NOT_CALLED"


@pytest.mark.asyncio
async def test_run_agent_성공시_errorCode는_None():
    request = AgentNodeRequest(nodeId="node-1", renderedPrompt="Hello", tools=[])

    async def _fake_run_async(*args, **kwargs):
        part = MagicMock()
        part.text = "ok"
        content = MagicMock()
        content.parts = [part]
        event = MagicMock()
        event.is_final_response.return_value = True
        event.content = content
        yield event

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock()

    with (
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service,
        )

    assert result.success is True
    assert result.errorCode is None


# ---------------------------------------------------------------------------
# HTTP 에러 경로도 본문에 errorCode를 싣는다
# ---------------------------------------------------------------------------

def test_크레덴셜_누락_400_본문에_errorCode가_실린다():
    client = TestClient(app)
    response = client.post(
        "/v1/execute",
        json={"nodeId": "node-1", "renderedPrompt": "Hello"},
        headers={"X-LLM-Provider": "CLAUDE", "X-User-Id": "test-user"},
    )

    assert response.status_code == 400
    assert response.json()["errorCode"] == "MISSING_CREDENTIAL"
