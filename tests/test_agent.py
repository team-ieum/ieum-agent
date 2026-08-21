"""
tests/test_agent.py

core/agent.py의 resolve_model() 및 run_agent() 함수에 대한 단위 테스트.
- resolve_model(): provider 문자열 → 모델 ID 변환 검증
- run_agent(): ADK 실행 성공/실패, os.environ 복원, Lock 동작, MongoDB 로깅 실패 처리
모든 외부 의존성(ADK, MongoDB)은 mock으로 처리.
"""

import asyncio
import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from common.error_code import ErrorCode
from core.agent import run_agent
from agents.base import _bind_notion_token
from core.provider_config import resolve_model
from core.env_lock import _env_locks
from core.config import settings
from core.provider_config import MODEL_MAP as _MODEL_MAP, ENV_KEY_MAP as _ENV_KEY_MAP
from tools import get_tools_for_request


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
    assert resolve_model("UNKNOWN") == settings.GEMINI_DEFAULT_MODEL


def test_resolve_model_empty_string_returns_default():
    assert resolve_model("") == settings.GEMINI_DEFAULT_MODEL


# --- IEUM-AI-57: 승격 축소 + 폐기 강등 ---

def test_resolve_model_promotes_only_gemini_2_5_flash():
    """hang 이력이 있는 gemini-2.5-flash만 기본 모델로 승격한다."""
    assert resolve_model("GEMINI", "gemini-2.5-flash") == settings.GEMINI_DEFAULT_MODEL


def test_resolve_model_keeps_other_gemini_2_models(monkeypatch):
    """gemini-2.5-pro 같은 다른 2.x는 존중한다(카탈로그 확장 전제). 폐기 판정은 끈다."""
    import litellm
    monkeypatch.setitem(litellm.model_cost, "gemini-2.5-pro", {})
    assert resolve_model("GEMINI", "gemini-2.5-pro") == "gemini-2.5-pro"


def test_resolve_model_demotes_deprecated_model(monkeypatch):
    """litellm deprecation_date가 지난 모델은 provider 기본 모델로 강등한다."""
    import litellm
    monkeypatch.setitem(litellm.model_cost, "claude-old-model", {"deprecation_date": "2020-01-01"})
    assert resolve_model("CLAUDE", "claude-old-model") == settings.CLAUDE_DEFAULT_MODEL


def test_resolve_model_passes_future_deprecation(monkeypatch):
    import litellm
    monkeypatch.setitem(litellm.model_cost, "claude-future-model", {"deprecation_date": "2999-01-01"})
    assert resolve_model("CLAUDE", "claude-future-model") == "claude-future-model"


def test_resolve_model_passes_unregistered_model(monkeypatch):
    """litellm에 없는 모델(자체 호스팅·신모델)은 판정 불가 → 그대로 통과."""
    import litellm
    monkeypatch.delitem(litellm.model_cost, "my-custom-model", raising=False)
    assert resolve_model("OPENAI", "my-custom-model") == "my-custom-model"


def test_resolve_model_passes_malformed_deprecation_date(monkeypatch):
    import litellm
    monkeypatch.setitem(litellm.model_cost, "weird-model", {"deprecation_date": "not-a-date"})
    assert resolve_model("OPENAI", "weird-model") == "weird-model"


def test_resolve_model_real_litellm_entry_claude_sonnet_4_20250514_is_demoted():
    """실데이터 회귀 가드: BE 카탈로그가 광고하던 claude-sonnet-4-20250514는 2026-06-15 폐기됐다."""
    assert resolve_model("CLAUDE", "claude-sonnet-4-20250514") == settings.CLAUDE_DEFAULT_MODEL


def test_model_map_reflects_settings():
    """MODEL_MAP이 settings의 모델명 설정과 일치한다."""
    assert _MODEL_MAP["CLAUDE"] == settings.CLAUDE_DEFAULT_MODEL
    assert _MODEL_MAP["OPENAI"] == settings.OPENAI_DEFAULT_MODEL
    assert _MODEL_MAP["GEMINI"] == settings.GEMINI_DEFAULT_MODEL


def test_env_key_map_has_known_providers():
    """ENV_KEY_MAP이 알려진 provider 환경변수 키를 포함한다."""
    assert _ENV_KEY_MAP["CLAUDE"] == "ANTHROPIC_API_KEY"
    assert _ENV_KEY_MAP["OPENAI"] == "OPENAI_API_KEY"
    assert _ENV_KEY_MAP["GEMINI"] == "GOOGLE_API_KEY"


def test_bind_notion_token_preserves_config_binding():
    """config로 일부 인자가 바인딩된 Notion 도구에도 token 바인딩이 적용된다."""
    tools = get_tools_for_request([
        {
            "name": "builtin:notion_create_page",
            "config": {
                "parent_page_id": "page-id",
                "title": "테스트 제목",
            },
        }
    ])

    bound_tools = _bind_notion_token(tools, "secret-token")
    fn = getattr(bound_tools[0], "func", None) or getattr(bound_tools[0], "_func", None)
    params = list(inspect.signature(fn).parameters.keys())

    assert "token" not in params
    assert "parent_page_id" not in params
    assert "title" not in params
    assert "content" in params


def test_resolve_model_uses_settings_default():
    """알 수 없는 provider는 settings.GEMINI_DEFAULT_MODEL을 기본값으로 반환한다."""
    assert resolve_model("UNKNOWN") == settings.GEMINI_DEFAULT_MODEL


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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is True
    assert result.output == ""


# ---------------------------------------------------------------------------
# run_agent() - os.environ 복원 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_commercial_no_env_injection():
    """CLAUDE/OPENAI + api_key는 LiteLlm(api_key=...) 인스턴스로 키를 전달받으므로
    os.environ에는 전혀 주입되지 않는다(uses_env_key()가 False를 반환).
    os.environ 오염 제거가 목적이므로 실행 전/도중/후 모두 env에 키가 없어야 한다.
    """
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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="CLAUDE", api_key="sk-test", user_id="test-user",
            session_service=mock_session_service
        )

    # LiteLlm 인스턴스가 키를 직접 받으므로 실행 도중에도 env에는 주입되지 않는다
    assert captured_env_values["during"] is None
    # 실행 후에도 여전히 env에는 키가 없어야 한다
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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="CLAUDE", api_key="sk-test", user_id="test-user",
            session_service=mock_session_service
        )

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
            patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
            patch("agents.execute.factory.Runner", return_value=mock_runner),
            patch("agents.execute.factory.get_tools_for_request", return_value=[]),
            patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
        ):
            await run_agent(
                request, provider="OPENAI", api_key="new-key", user_id="test-user",
                session_service=mock_session_service
            )
    finally:
        # 테스트 환경 정리
        os.environ.pop(env_key, None)

    assert os.environ.get(env_key) is None  # finally에서 제거됨


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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is False
    assert result.errorMessage == ErrorCode.AGENT_EXECUTION_FAILED.message


@pytest.mark.asyncio
async def test_run_agent_rate_limit_returns_rate_limited_message():
    """429(rate limit) 예외 시 errorMessage가 RATE_LIMITED로 구분된다."""
    request = _make_request()

    class _RateLimitError(Exception):
        def __init__(self):
            self.code = 429

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=_RateLimitError())

    with (
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is False
    assert result.errorMessage == ErrorCode.RATE_LIMITED.message


@pytest.mark.asyncio
async def test_run_agent_exception_metadata_not_exposed():
    """실패 시 metadata에 내부 에러 detail이 노출되지 않는다."""
    request = _make_request()

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(
        side_effect=RuntimeError("internal secret error detail")
    )

    with (
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    # metadata가 None이거나, 있더라도 내부 에러 문자열을 포함하지 않아야 한다
    if result.metadata is not None:
        metadata_str = str(result.metadata)
        assert "internal secret error detail" not in metadata_str


# ---------------------------------------------------------------------------
# run_agent() - Lock 동작 검증
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_commercial_skips_env_lock():
    """CLAUDE/OPENAI + api_key는 LiteLlm 인스턴스로 키를 주입받아 os.environ을
    건드리지 않으므로(uses_env_key()=False), env_lock도 획득할 필요가 없다.
    env_key 매핑 자체는 존재하지만(_ENV_KEY_MAP["CLAUDE"]), run_agent는
    uses_env_key() 결과에 따라 Lock 획득을 건너뛰어야 한다.
    """
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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
        patch.dict("core.env_lock._env_locks", {env_key: spy_lock}),
    ):
        result = await run_agent(
            request, provider="CLAUDE", api_key="sk-test", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is True
    assert acquired_count["value"] == 0, "LiteLlm 인스턴스 주입 방식이라 env_lock을 획득하지 않아야 한다"


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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch("core.agent.execution_logs.insert_one", new=AsyncMock()),
    ):
        result = await run_agent(
            request, provider="UNKNOWN", api_key="", user_id="test-user",
            session_service=mock_session_service
        )

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
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=mock_runner),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch(
            "core.agent.execution_logs.insert_one",
            new=AsyncMock(side_effect=Exception("MongoDB connection refused")),
        ),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is True
    assert result.output == "result despite db failure"


@pytest.mark.asyncio
async def test_run_agent_mongodb_failure_on_error_result_still_returns():
    """ADK 실패 + MongoDB 로깅 실패 조합에서도 실패 결과를 반환한다."""
    request = _make_request()

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(side_effect=RuntimeError("adk error"))

    with (
        patch("agents.execute.factory.LlmAgent", return_value=MagicMock()),
        patch("agents.execute.factory.Runner", return_value=MagicMock()),
        patch("agents.execute.factory.get_tools_for_request", return_value=[]),
        patch(
            "core.agent.execution_logs.insert_one",
            new=AsyncMock(side_effect=Exception("MongoDB down")),
        ),
    ):
        result = await run_agent(
            request, provider="GEMINI", api_key="test-key", user_id="test-user",
            session_service=mock_session_service
        )

    assert result.success is False
    assert result.errorMessage == ErrorCode.AGENT_EXECUTION_FAILED.message
