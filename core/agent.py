import asyncio
import functools
import logging
import time
from datetime import datetime, timezone

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult, UsageRecord
from common.error_code import ErrorCode
from common.exception import is_rate_limit_error
from core.config import settings
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from core.model_factory import uses_env_key
from db.mongodb import execution_logs
from google.adk.sessions import BaseSessionService
from agents.execute.factory import run_simple_agent, run_react_agent, ToolNotCalledError
from core.execution_guard import ExecutionGuard, ExecutionGuardError
from core.output_validator import OutputValidator

logger = logging.getLogger(__name__)


async def save_execution_log(
    user_id: str,
    node_id: str,
    workflow_execution_id: str | None,
    provider: str,
    model: str,
    agent_type: str,
    result: AgentExecutionResult,
    duration_ms: int,
    key_mode: str | None = None,
):
    # 민감 정보가 포함되어 누출되는 것을 차단하기 위해 로그 저장 시 엄격한 마스킹 수행
    masked_output = OutputValidator.mask_log_content(result.output)
    masked_err = OutputValidator.mask_log_content(result.errorMessage)
    masked_tools = OutputValidator.mask_log_content(
        [tc.model_dump() for tc in result.toolCalls] if result.toolCalls else []
    )

    await execution_logs.insert_one({
        "userId": user_id,
        "nodeId": node_id,
        "workflowExecutionId": workflow_execution_id,
        "provider": provider,
        "model": model,
        "agentType": agent_type,
        "status": result.status,
        "success": result.success,
        "output": masked_output,
        "errorMessage": masked_err,
        # ErrorCode enum 이름. 왜 이 실행이 재시도됐는지/안 됐는지를 로그만으로 추적하려면 필요하다.
        "errorCode": result.errorCode,
        "toolCalls": masked_tools,
        "usage": result.usage.model_dump() if result.usage else None,
        "keyMode": key_mode,
        "durationMs": duration_ms,
        "createdAt": datetime.now(timezone.utc),
    })


async def run_agent(
    request: AgentNodeRequest,
    provider: str,
    api_key: str | None,
    user_id: str,
    user_role: str | None = None,
    key_mode: str | None = None,
    google_access_token: str | None = None,
    notion_token: str | None = None,
    github_token: str | None = None,
    session_service: BaseSessionService | None = None,
) -> AgentExecutionResult:
    start = time.monotonic()
    result = AgentExecutionResult(success=False)

    # platform 모드는 노드의 모델 지정을 무시하고 provider 기본모델로 강제한다.
    # (베타 비용 통제 — 고가 모델 우회 차단. BE가 provider를 GEMINI로 강제해 보냄)
    model_override = None if key_mode == "platform" else request.model
    if key_mode == "platform" and request.model:
        # 강등이 조용히 일어나면 "왜 내 모델이 바뀌었나" CS 추적이 불가하므로 흔적을 남긴다.
        logger.debug("platform 모드: 노드 지정 모델 %s 를 provider 기본모델로 강등", request.model)

    # 1. Execution Guard (사전 무결성/보안 필터)
    # SSRF 검사의 DNS 조회(socket.gethostbyname)가 동기 블로킹이므로
    # 이벤트 루프를 막지 않도록 스레드 풀로 오프로드한다.
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                ExecutionGuard.validate_execution,
                request,
                google_access_token=google_access_token,
                notion_token=notion_token,
                github_token=github_token,
            ),
        )
    except ExecutionGuardError as e:
        logger.warning("Execution guard rejected request: %s", e)
        duration_ms = int((time.monotonic() - start) * 1000)
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=str(e),
            # 가드 차단은 요청 자체가 잘못된 것이라 재시도해도 동일하게 막힌다(BE: UNKNOWN → 재시도 안 함).
            errorCode=ErrorCode.AGENT_EXECUTION_FAILED.name,
        )
        # 차단에 따른 히스토리 로그 저장
        try:
            await save_execution_log(
                user_id=user_id,
                node_id=request.nodeId,
                workflow_execution_id=request.workflowExecutionId,
                provider=provider,
                model=resolve_model(provider, model_override),
                agent_type=request.agentType,
                result=result,
                duration_ms=duration_ms,
                key_mode=key_mode,
            )
        except Exception:
            # keyMode가 빌링 귀속 감사필드가 되면서 이 경로의 로그 유실도 흔적이 필요하다 (정상 경로와 동일 패턴).
            logger.warning("Failed to save execution log for node %s", request.nodeId, exc_info=True)
        return result

    env_key = resolve_env_key(provider)
    # Gemini(CustomGemini 직접 주입) 및 자체 LLM(엔드포인트 자격증명 사용)은 os.environ을 건드리지 않으므로 Lock을 잡지 않습니다.
    lock = get_env_lock(env_key) if (env_key and uses_env_key(provider, api_key, user_role)) else None

    model = resolve_model(provider, model_override)

    try:
        async def _execute() -> tuple[str, int, int, int]:
            if request.agentType == "react":
                return await run_react_agent(
                    model=model,
                    request=request,
                    api_key=api_key,
                    env_key=env_key,
                    user_id=user_id,
                    provider=provider,
                    user_role=user_role,
                    google_access_token=google_access_token,
                    notion_token=notion_token,
                    github_token=github_token,
                    session_service=session_service,
                )
            else:
                return await run_simple_agent(
                    model=model,
                    request=request,
                    api_key=api_key,
                    env_key=env_key,
                    user_id=user_id,
                    provider=provider,
                    user_role=user_role,
                    session_service=session_service,
                )

        if lock:
            async with lock:
                output, input_tokens, output_tokens, total_tokens = await asyncio.wait_for(
                    _execute(), timeout=settings.AGENT_TIMEOUT_SECONDS
                )
        else:
            output, input_tokens, output_tokens, total_tokens = await asyncio.wait_for(
                _execute(), timeout=settings.AGENT_TIMEOUT_SECONDS
            )

        usage = UsageRecord(
            promptTokens=input_tokens,
            completionTokens=output_tokens,
            totalTokens=total_tokens or (input_tokens + output_tokens),
        ) if (input_tokens or output_tokens) else None

        if not (output or "").strip():
            logger.warning(
                "Agent completed with empty output for node %s (provider=%s, model=%s). "
                "도구 호출만 수행하고 최종 텍스트를 생성하지 못했을 수 있습니다.",
                request.nodeId, provider, model,
            )

        result = AgentExecutionResult(
            success=True,
            status="COMPLETED",
            output=output,
            usage=usage,
        )

    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "Agent execution timed out after %ss for node %s",
            settings.AGENT_TIMEOUT_SECONDS, request.nodeId,
        )
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=ErrorCode.AGENT_TIMEOUT.message,
            errorCode=ErrorCode.AGENT_TIMEOUT.name,
        )

    except ToolNotCalledError as e:
        logger.warning("Tool not called for node %s: %s", request.nodeId, e)
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=ErrorCode.AGENT_TOOL_NOT_CALLED.message,
            errorCode=ErrorCode.AGENT_TOOL_NOT_CALLED.name,
        )

    except Exception as e:
        logger.exception("Agent execution failed")
        error_code = ErrorCode.RATE_LIMITED if is_rate_limit_error(e) else ErrorCode.AGENT_EXECUTION_FAILED
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=error_code.message,
            errorCode=error_code.name,
        )

    duration_ms = int((time.monotonic() - start) * 1000)

    try:
        await save_execution_log(
            user_id=user_id,
            node_id=request.nodeId,
            workflow_execution_id=request.workflowExecutionId,
            provider=provider,
            model=model,
            agent_type=request.agentType,
            result=result,
            duration_ms=duration_ms,
            key_mode=key_mode,
        )
    except Exception:
        logger.warning("Failed to save execution log for node %s", request.nodeId, exc_info=True)

    # 2. 클라이언트 응답 반환을 위한 최종 마스킹 (토큰 유출 방지)
    if result.output:
        result.output = OutputValidator.mask_response_content(result.output)
    if result.errorMessage:
        result.errorMessage = OutputValidator.mask_response_content(result.errorMessage)
    if result.toolCalls:
        for tc in result.toolCalls:
            if hasattr(tc, "arguments") and tc.arguments:
                tc.arguments = OutputValidator.mask_response_content(tc.arguments)

    return result
