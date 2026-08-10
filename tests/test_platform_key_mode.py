"""베타 플랫폼 키 모드 (X-Key-Mode: platform) 테스트."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from api.middleware.credential import get_llm_credentials
from api.routes.chat import _build_chat_kwargs
from api.schemas.chat import ChatRequest
from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from core.agent import run_agent, save_execution_log
from core.config import settings
from core.workflow_chat import _save_chat_log
from core.workflow_generator import _save_generate_workflow_log
from main import app


def test_platform_gemini_api_key_setting_exists():
    # 기본값은 빈 문자열(= platform 모드 비활성). .env 없이도 str 타입 보장.
    assert isinstance(settings.PLATFORM_GEMINI_API_KEY, str)


async def _call_middleware(**overrides):
    kwargs = dict(
        x_llm_provider="CLAUDE",
        x_llm_api_key=None,
        x_user_id="user-1",
        x_user_role=None,
        x_google_access_token=None,
        x_notion_token=None,
        x_github_token=None,
        x_key_mode=None,
    )
    kwargs.update(overrides)
    return await get_llm_credentials(**kwargs)


@pytest.mark.asyncio
async def test_platform_mode_swaps_to_platform_gemini_key():
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"):
        creds = await _call_middleware(x_key_mode="platform")
    # provider가 원래 CLAUDE여도 GEMINI + 플랫폼 키로 강제된다
    assert creds["provider"] == "GEMINI"
    assert creds["api_key"] == "pk-test"
    assert creds["key_mode"] == "platform"


@pytest.mark.asyncio
async def test_platform_mode_user_key_wins():
    # 사용자 키가 함께 오면 사용자 키 우선 (스왑·key_mode 미적용)
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"):
        creds = await _call_middleware(x_key_mode="platform", x_llm_api_key="sk-user")
    assert creds["provider"] == "CLAUDE"
    assert creds["api_key"] == "sk-user"
    assert creds["key_mode"] is None


@pytest.mark.asyncio
async def test_platform_mode_rejected_when_key_unconfigured():
    # 플랫폼 키 미설정이면 기존 MISSING_CREDENTIAL 400 유지 (조용한 폴백 금지)
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", ""):
        with pytest.raises(HTTPException) as exc:
            await _call_middleware(x_key_mode="platform")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_no_key_mode_header_keeps_existing_behavior():
    # 회귀 가드: 헤더 없음 + 키 없음 → 기존 400
    with pytest.raises(HTTPException) as exc:
        await _call_middleware()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_no_key_mode_header_with_user_key_unchanged():
    # 회귀 가드: 기존 BYOK 경로 무변경, key_mode는 None
    creds = await _call_middleware(x_llm_api_key="sk-user")
    assert creds["provider"] == "CLAUDE"
    assert creds["api_key"] == "sk-user"
    assert creds["key_mode"] is None


# ---------------------------------------------------------------------------
# Task 3: run_agent + save_execution_log 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_execution_log_records_key_mode():
    mock_insert = AsyncMock()
    with patch("core.agent.execution_logs") as mock_logs:
        mock_logs.insert_one = mock_insert
        await save_execution_log(
            user_id="user-1",
            node_id="n1",
            workflow_execution_id=None,
            provider="GEMINI",
            model="gemini-3.5-flash",
            agent_type="simple",
            result=AgentExecutionResult(success=True, status="COMPLETED", output="ok"),
            duration_ms=10,
            key_mode="platform",
        )
    doc = mock_insert.call_args.args[0]
    assert doc["keyMode"] == "platform"


@pytest.mark.asyncio
async def test_run_agent_platform_mode_forces_default_model():
    # platform 모드에서는 노드가 gemini-3.5-pro를 지정해도 기본모델(flash)로 강제된다
    mock_simple = AsyncMock(return_value=("결과", 100, 50, 150))
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="안녕", model="gemini-3.5-pro")
    with patch("core.agent.run_simple_agent", mock_simple), \
         patch("core.agent.execution_logs") as mock_logs:
        mock_logs.insert_one = AsyncMock()
        result = await run_agent(
            request, "GEMINI", "pk-test", "user-1", key_mode="platform",
        )
    assert result.success is True
    assert mock_simple.call_args.kwargs["model"] == settings.GEMINI_DEFAULT_MODEL
    # 로그에도 keyMode가 남는다
    assert mock_logs.insert_one.call_args.args[0]["keyMode"] == "platform"


@pytest.mark.asyncio
async def test_run_agent_byok_mode_respects_model_override():
    # 회귀 가드: key_mode 없으면 기존처럼 노드 지정 모델(gemini-3.x)을 존중한다
    mock_simple = AsyncMock(return_value=("결과", 100, 50, 150))
    request = AgentNodeRequest(nodeId="n1", renderedPrompt="안녕", model="gemini-3.5-pro")
    with patch("core.agent.run_simple_agent", mock_simple), \
         patch("core.agent.execution_logs") as mock_logs:
        mock_logs.insert_one = AsyncMock()
        await run_agent(request, "GEMINI", "sk-user", "user-1")
    assert mock_simple.call_args.kwargs["model"] == "gemini-3.5-pro"
    assert mock_logs.insert_one.call_args.args[0]["keyMode"] is None


# ---------------------------------------------------------------------------
# Task 4: chat/generate/modify key_mode 스레딩 테스트
# ---------------------------------------------------------------------------

def test_build_chat_kwargs_passes_key_mode():
    request = ChatRequest(prompt="워크플로우 만들어줘", availableIntegrations=[], unavailableIntegrations=[])
    credentials = {"provider": "GEMINI", "api_key": "pk", "user_id": "u1", "key_mode": "platform"}
    kwargs = _build_chat_kwargs(request, credentials)
    assert kwargs["key_mode"] == "platform"


@pytest.mark.asyncio
async def test_generate_log_records_key_mode():
    with patch("core.workflow_generator.generate_workflow_logs") as mock_logs:
        mock_logs.insert_one = AsyncMock()
        await _save_generate_workflow_log(
            prompt="p", provider="GEMINI", model="gemini-3.5-flash",
            success=True, duration_ms=10, key_mode="platform",
        )
    assert mock_logs.insert_one.call_args.args[0]["keyMode"] == "platform"


@pytest.mark.asyncio
async def test_chat_log_records_key_mode():
    with patch("core.workflow_chat.chat_logs") as mock_logs:
        mock_logs.insert_one = AsyncMock()
        await _save_chat_log(
            prompt="p", provider="GEMINI", model="gemini-3.5-flash",
            user_id="u1", success=True, duration_ms=10, key_mode="platform",
        )
    assert mock_logs.insert_one.call_args.args[0]["keyMode"] == "platform"


# ---------------------------------------------------------------------------
# Task 5: HTTP 통합 테스트 — platform 모드 E2E + usage 회귀 가드
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_platform_mode_end_to_end():
    """X-Key-Mode: platform + 키 헤더 없음 → 200, GEMINI 스왑, usage 포함(BE 토큰 차감 입력), keyMode 로깅."""
    mock_simple = AsyncMock(return_value=("결과", 100, 50, 150))
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"), \
         patch("core.agent.run_simple_agent", mock_simple), \
         patch("core.agent.execution_logs") as mock_logs:
        mock_logs.insert_one = AsyncMock()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/execute",
                json={"nodeId": "n1", "renderedPrompt": "안녕", "agentType": "simple"},
                headers={"X-LLM-Provider": "CLAUDE", "X-User-Id": "u1", "X-Key-Mode": "platform"},
            )
    assert resp.status_code == 200
    body = resp.json()
    # usage는 BE 총량 토큰 차감의 입력값 — 회귀 금지 (IEUM-BE-43 계약)
    assert body["usage"]["totalTokens"] == 150
    doc = mock_logs.insert_one.call_args.args[0]
    assert doc["keyMode"] == "platform"
    assert doc["provider"] == "GEMINI"


@pytest.mark.asyncio
async def test_execute_without_key_mode_and_key_still_400():
    """회귀 가드: 헤더도 키도 없으면 기존 400 유지."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/execute",
            json={"nodeId": "n1", "renderedPrompt": "안녕"},
            headers={"X-LLM-Provider": "CLAUDE", "X-User-Id": "u1"},
        )
    assert resp.status_code == 400


# ---- PR #40 리뷰 반영: 미들웨어 방어·진단 로그 ----

@pytest.mark.asyncio
async def test_platform_mode_self_hosted_priority_wins():
    # self-hosted 자격(허용 role + 엔드포인트 활성) 계정에는 platform 위임이 와도 스왑하지 않는다.
    # 우선순위: BYOK > self-hosted > platform — 미들웨어 자체 방어 (BE 계약과 별개).
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", "pk-test"), \
         patch.object(settings, "SELF_HOSTED_LLM_BASE_URL", "http://localhost:8001/v1"), \
         patch.object(settings, "SELF_HOSTED_LLM_MODEL", "test-model"):
        creds = await _call_middleware(x_key_mode="platform", x_user_role="ROLE_TESTER")
    assert creds["provider"] == "CLAUDE"
    assert creds["api_key"] is None
    assert creds["key_mode"] is None


@pytest.mark.asyncio
async def test_unrecognized_key_mode_warns_and_ignores(caplog):
    # 미인식 X-Key-Mode 값은 무시하되, keyMode 감사필드 오염 추적을 위해 warning을 남긴다.
    import logging
    with caplog.at_level(logging.WARNING, logger="api.middleware.credential"):
        creds = await _call_middleware(x_key_mode="Platform", x_llm_api_key="sk-user")
    assert creds["key_mode"] is None
    assert creds["api_key"] == "sk-user"
    assert any("X-Key-Mode" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_platform_mode_unconfigured_logs_operator_error(caplog):
    # 플랫폼 키 미프로비저닝은 유저 400과 byte 동일 — 운영자 추적용 error 로그가 남아야 한다.
    import logging
    with patch.object(settings, "PLATFORM_GEMINI_API_KEY", ""), \
         caplog.at_level(logging.ERROR, logger="api.middleware.credential"):
        with pytest.raises(HTTPException) as exc:
            await _call_middleware(x_key_mode="platform")
    assert exc.value.status_code == 400
    assert any("PLATFORM_GEMINI_API_KEY" in r.getMessage() for r in caplog.records)
