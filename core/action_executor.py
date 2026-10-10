"""앱 도구 하나를 LLM 없이 실행한다 — POST /v1/actions/execute의 실행부(ACTION 노드, ADR-005).

도구 함수는 ADK와 무관한 일반 함수이고, 실패해도 예외 대신 {"error": ...} JSON을 돌려준다.
이 모듈은 (1) 가드 → (2) config 거르기·토큰 주입 → (3) 호출 → (4) 반환 JSON 해석 → (5) 로그를 맡는다."""
import asyncio
import functools
import inspect
import json
import logging
import time

from api.schemas.request import ActionExecutionRequest
from api.schemas.response import ActionExecutionResult, AgentExecutionResult
from common.error_code import ErrorCode
from core.agent import save_execution_log
from core.config import settings
from core.execution_guard import ExecutionGuard, ExecutionGuardError
from core.output_validator import OutputValidator

logger = logging.getLogger(__name__)


def _failure(code: ErrorCode, message: str) -> ActionExecutionResult:
    return ActionExecutionResult(
        success=False,
        errorMessage=OutputValidator.mask_response_content(message),
        errorCode=code.name,
    )


def _injected_args(tool_key: str, *, google_access_token, notion_token, github_token) -> dict:
    """헤더로 받은 도구 인증값 → 함수 인자. 서비스는 tool_key 접두사로 정한다(ExecutionGuard 토큰 검사와 같은 규칙).
    notion·github 함수는 둘 다 `token`을 받으므로 인자 이름으로 갈라선 안 된다 — 토큰이 서로 섞인다."""
    if tool_key.startswith("builtin:google_"):
        return {"access_token": google_access_token}
    if tool_key.startswith("builtin:notion_"):
        return {"token": notion_token}
    if tool_key.startswith("builtin:github_"):
        return {"token": github_token}
    return {}


async def _call(fn, kwargs: dict):
    if inspect.iscoroutinefunction(fn):
        return await fn(**kwargs)
    # ponytail: to_thread는 wait_for 타임아웃으로 취소되지 않는다 — 스레드는 끝까지 돈다. 동기 도구는
    # json_parse·text_extract·date_format뿐이라 짧다. 긴 동기 도구가 생기면 별도 실행기·취소 신호가 필요하다.
    return await asyncio.to_thread(fn, **kwargs)  # 이벤트 루프를 막지 않는다


def _to_result(raw) -> ActionExecutionResult:
    """도구 반환(JSON 문자열) → 응답. `error` 키가 있으면 값이 비어 있어도 실패다."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return _failure(ErrorCode.AGENT_EXECUTION_FAILED, "도구가 JSON 객체가 아닌 값을 반환했습니다.")
    if "error" in data:
        return _failure(ErrorCode.ACTION_TOOL_FAILED, str(data["error"]) or ErrorCode.ACTION_TOOL_FAILED.message)
    data.pop("success", None)
    return ActionExecutionResult(success=True, output=OutputValidator.mask_response_content(data))


async def _execute(fn, request: ActionExecutionRequest, google_access_token, notion_token, github_token):
    tools = [{"name": request.toolKey, "config": request.config}]
    try:
        # SSRF 검사의 DNS 조회가 동기 블로킹이라 스레드 풀로 오프로드한다(run_agent와 같다).
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                ExecutionGuard.validate_tools, tools,
                google_access_token=google_access_token, notion_token=notion_token, github_token=github_token,
            ),
        )
    except ExecutionGuardError as e:
        logger.warning("Execution guard rejected action %s: %s", request.toolKey, e)
        return _failure(ErrorCode.AGENT_EXECUTION_FAILED, str(e))

    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in request.config.items() if k in params}  # webhookCredentialId·_names 등은 버린다
    kwargs.update(_injected_args(
        request.toolKey, google_access_token=google_access_token, notion_token=notion_token,
        github_token=github_token))  # 겹치면 주입값이 이긴다
    missing = [n for n, p in params.items() if p.default is inspect.Parameter.empty and n not in kwargs]
    if missing:
        return _failure(ErrorCode.AGENT_EXECUTION_FAILED, f"필수 입력이 없습니다: {', '.join(missing)}")

    try:
        raw = await asyncio.wait_for(_call(fn, kwargs), timeout=settings.AGENT_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("Action %s timed out after %ss", request.toolKey, settings.AGENT_TIMEOUT_SECONDS)
        return _failure(ErrorCode.AGENT_TIMEOUT, ErrorCode.AGENT_TIMEOUT.message)
    except Exception:
        logger.exception("Action %s failed", request.toolKey)
        return _failure(ErrorCode.AGENT_EXECUTION_FAILED, ErrorCode.AGENT_EXECUTION_FAILED.message)
    return _to_result(raw)


async def run_action(
    fn,
    request: ActionExecutionRequest,
    user_id: str,
    *,
    google_access_token: str | None = None,
    notion_token: str | None = None,
    github_token: str | None = None,
) -> ActionExecutionResult:
    start = time.monotonic()
    result = await _execute(fn, request, google_access_token, notion_token, github_token)
    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        # 기존 로그 함수를 그대로 쓴다. config는 기록하지 않는다(webhook URL 등 비밀이 들어 있다).
        await save_execution_log(
            user_id=user_id,
            node_id=request.nodeId,
            workflow_execution_id=None,
            provider="ACTION",
            model=request.toolKey,
            agent_type="action",
            result=AgentExecutionResult(
                success=result.success,
                status="COMPLETED" if result.success else "ERROR",
                output=json.dumps(result.output, ensure_ascii=False) if result.output is not None else None,
                errorMessage=result.errorMessage,
                errorCode=result.errorCode,
            ),
            duration_ms=duration_ms,
        )
    except Exception:
        logger.warning("Failed to save action execution log for node %s", request.nodeId, exc_info=True)
    return result
