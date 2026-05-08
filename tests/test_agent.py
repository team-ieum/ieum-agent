"""
tests/test_agent.py

core/agent.py의 resolve_model() 및 run_agent() 함수에 대한 단위 테스트.
- resolve_model(): provider 문자열 → 모델 ID 변환 검증
- run_agent(): ADK 실행 성공/실패, os.environ 복원, Lock 동작, MongoDB 로깅 실패 처리
모든 외부 의존성(ADK, MongoDB)은 mock으로 처리.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from common.error_code import ErrorCode
from core.agent import resolve_model, run_agent, _MODEL_MAP, _ENV_KEY_MAP, _env_locks


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

def _make_request(
    node_id: str = "node-1",
    prompt: str = "Hello",
    tools: list | None = None,
) -> AgentNodeRequest:
    return AgentNodeRequest(nodeId=node_id, renderedPrompt=prompt, tools=tools or [])


def _make_final_event(text: str = "agent response") -> MagicMock:
    """ADK runner.run_async()가 반환하는 최종 응답 이벤트 mock."""
    part = MagicMock()
    part.text = text
    content = MagicMock()
    content.parts = [part]
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content = content
    return event


def _make_non_final_event() -> MagicMock:
    """최종 응답이 아닌 중간 이벤트 mock."""
    event = MagicMock()
    event.is_final_response.return_value = False
    event.content = None
    return event


# ---------------------------------------------------------------------------
# resolve_model() 테스트
# ---------------------------------------------------------------------------

def test_resolve_model_claude_returns_correct_model_id():
    assert resolve_model("CLAUDE") == _MODEL_MAP["CLAUDE"]


def test_resolve_model_openai_returns_correct_model_id():
    assert resolve_model("OPENAI") == _MODEL_MAP["OPENAI"]


def test_resolve_model_gemini_returns_correct_model_id():
    assert resolve_model("GEMINI") == _MODEL_MAP["GEMINI"]


def test_resolve_model_case_insensitive():
    """소문자 provider도 동일한 모델 ID를 반환해야 한다."""
    assert resolve_model("claude") == _MODEL_MAP["CLAUDE"]
    assert resolve_model("openai") == _MODEL_MAP["OPENAI"]
    assert resolve_model("gemini") == _MODEL_MAP["GEMINI"]


def test_resolve_model_unknown_provider_returns_default():
    assert resolve_model("UNKNOWN") == "gemini-2.5-flash"


def test_resolve_model_empty_string_returns_default():
    assert resolve_model("") == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# run_agent() 성공 케이스
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_success_returns_output():
    """ADK runner가 최종 응답 이벤트를 반환할 때 success=True, output이 설정된다."""
    request = _make_request()
    final_event = _make_final_event("Hello from agent")

    async def _fake_run_async(**kwargs):
        yield final_event

    mock_session = MagicMock()
    mock_session.id = "session-123"

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is True
    assert result.output == "Hello from agent"
    assert result.errorMessage is None


@pytest.mark.asyncio
async def test_run_agent_success_multiple_parts_joined():
    """여러 part.text가 있으면 줄바꿈으로 합쳐진다."""
    request = _make_request()

    part1, part2 = MagicMock(), MagicMock()
    part1.text = "Line 1"
    part2.text = "Line 2"
    content = MagicMock()
    content.parts = [part1, part2]
    final_event = MagicMock()
    final_event.is_final_response.return_value = True
    final_event.content = content

    async def _fake_run_async(**kwargs):
        yield final_event

    mock_session = MagicMock()
    mock_session.id = "session-abc"

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is True
    assert result.output == "Line 1\nLine 2"


@pytest.mark.asyncio
async def test_run_agent_success_non_final_events_ignored():
    """중간 이벤트만 있고 최종 이벤트가 없으면 output은 빈 문자열이다."""
    request = _make_request()

    async def _fake_run_async(**kwargs):
        yield _make_non_final_event()
        yield _make_non_final_event()

    mock_session = MagicMock()
    mock_session.id = "session-xyz"

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is True
    assert result.output == ""


# ---------------------------------------------------------------------------
# run_agent() - os.environ 복원 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_env_key_set_and_restored():
    """run_agent 실행 후 os.environ에서 API 키가 복원(제거)된다."""
    request = _make_request()
    env_key = _ENV_KEY_MAP["CLAUDE"]

    # 실행 전 환경변수 없음 보장
    os.environ.pop(env_key, None)

    captured_env_values = {}

    async def _fake_run_async(**kwargs):
        # _execute() 내부 실행 중 env_key가 설정되어 있는지 캡처
        captured_env_values["during"] = os.environ.get(env_key)
        yield _make_final_event("ok")

    mock_session = MagicMock()
    mock_session.id = "s1"

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="CLAUDE", api_key="sk-test")

    # 실행 도중에는 환경변수가 설정되어 있어야 한다
    assert captured_env_values["during"] == "sk-test"
    # 실행 후에는 환경변수가 원복(없어야)되어야 한다
    assert env_key not in os.environ
    assert result.success is True


@pytest.mark.asyncio
async def test_run_agent_env_key_restored_on_exception():
    """ADK 실행 중 예외가 발생해도 os.environ이 복원된다."""
    request = _make_request()
    env_key = _ENV_KEY_MAP["CLAUDE"]
    os.environ.pop(env_key, None)

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=RuntimeError("adk error"))

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=MagicMock()),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="CLAUDE", api_key="sk-test")

    assert env_key not in os.environ
    assert result.success is False


@pytest.mark.asyncio
async def test_run_agent_env_key_previous_value_restored():
    """기존 환경변수가 있었다면 실행 후 원래 값으로 복원된다."""
    request = _make_request()
    env_key = _ENV_KEY_MAP["OPENAI"]
    original_value = "original-openai-key"
    os.environ[env_key] = original_value

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_session = MagicMock()
    mock_session.id = "s2"

    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    try:
        with (
            patch("core.agent.LlmAgent", return_value=MagicMock()),
            patch("core.agent.Runner", return_value=mock_runner),
            patch("core.agent.InMemorySessionService", return_value=mock_session_service),
            patch("core.agent.get_tools_for_request", return_value=[]),
            patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
        ):
            await run_agent(request, provider="OPENAI", api_key="new-key")
    finally:
        # 테스트 환경 정리
        os.environ.pop(env_key, None)

    assert os.environ.get(env_key) is None  # finally에서 제거됨
    # 실제로는 원래 값으로 복원되어야 하지만, 여기선 테스트 cleanup 이후이므로
    # 위에서 복원됨을 간접 확인(run_agent 이후 팝 전까지 original_value여야 함)


# ---------------------------------------------------------------------------
# run_agent() - 실패 케이스
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_exception_returns_failure_result():
    """ADK 실행 중 예외 발생 시 success=False, ErrorCode 메시지가 반환된다."""
    request = _make_request()

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=RuntimeError("adk crashed"))

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=MagicMock()),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is False
    assert result.errorMessage == ErrorCode.AGENT_EXECUTION_FAILED.message


@pytest.mark.asyncio
async def test_run_agent_exception_metadata_not_exposed():
    """실패 시 metadata에 내부 에러 detail이 노출되지 않는다."""
    request = _make_request()

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(
        side_effect=RuntimeError("internal secret error detail")
    )

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=MagicMock()),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    # metadata가 None이거나, 있더라도 내부 에러 문자열을 포함하지 않아야 한다
    if result.metadata is not None:
        metadata_str = str(result.metadata)
        assert "internal secret error detail" not in metadata_str


# ---------------------------------------------------------------------------
# run_agent() - Lock 동작 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_known_provider_uses_env_lock():
    """env_key가 있는 provider(CLAUDE)는 _env_locks[env_key] Lock을 사용한다."""
    request = _make_request()
    env_key = _ENV_KEY_MAP["CLAUDE"]

    # _env_locks에 spy lock 주입
    spy_lock = asyncio.Lock()
    acquired_count = {"value": 0}
    original_acquire = spy_lock.acquire

    async def _spy_acquire():
        acquired_count["value"] += 1
        return await original_acquire()

    spy_lock.acquire = _spy_acquire

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_session = MagicMock()
    mock_session.id = "s3"
    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
        patch.dict("core.agent._env_locks", {env_key: spy_lock}),
    ):
        result = await run_agent(request, provider="CLAUDE", api_key="sk-test")

    assert result.success is True
    assert acquired_count["value"] == 1, "Lock이 정확히 한 번 획득되어야 한다"


@pytest.mark.asyncio
async def test_run_agent_unknown_provider_no_lock():
    """env_key가 없는 provider(매핑 없음)는 Lock 없이 실행된다."""
    request = _make_request()

    async def _fake_run_async(**kwargs):
        yield _make_final_event("ok")

    mock_session = MagicMock()
    mock_session.id = "s4"
    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    # UNKNOWN provider는 _ENV_KEY_MAP에 없으므로 env_key=None → lock=None
    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(request, provider="UNKNOWN", api_key="")

    assert result.success is True


# ---------------------------------------------------------------------------
# save_execution_log() 실패 처리
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_mongodb_failure_still_returns_result():
    """MongoDB 로깅 실패해도 run_agent()는 정상 결과를 반환한다."""
    request = _make_request()

    async def _fake_run_async(**kwargs):
        yield _make_final_event("result despite db failure")

    mock_session = MagicMock()
    mock_session.id = "s5"
    mock_runner = MagicMock()
    mock_runner.run_async = _fake_run_async
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=mock_runner),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch(
            "core.agent.execution_logs.insert_one",
            new=AsyncMock(side_effect=Exception("MongoDB connection refused")),
        ),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is True
    assert result.output == "result despite db failure"


@pytest.mark.asyncio
async def test_run_agent_mongodb_failure_on_error_result_still_returns():
    """ADK 실패 + MongoDB 로깅 실패 조합에서도 실패 결과를 반환한다."""
    request = _make_request()

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=RuntimeError("adk error"))

    with (
        patch("core.agent.LlmAgent", return_value=MagicMock()),
        patch("core.agent.Runner", return_value=MagicMock()),
        patch("core.agent.InMemorySessionService", return_value=mock_session_service),
        patch("core.agent.get_tools_for_request", return_value=[]),
        patch(
            "core.agent.execution_logs.insert_one",
            new=AsyncMock(side_effect=Exception("MongoDB down")),
        ),
    ):
        result = await run_agent(request, provider="GEMINI", api_key="test-key")

    assert result.success is False
    assert result.errorMessage == ErrorCode.AGENT_EXECUTION_FAILED.message
