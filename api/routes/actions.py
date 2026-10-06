import asyncio

from fastapi import APIRouter, Depends, Header

from api.middleware.credential import get_action_credentials
from api.schemas.request import ActionExecutionRequest
from api.schemas.response import ActionExecutionResult
from common.error_code import ErrorCode
from common.exception import CodedHTTPException
from core.action_executor import run_action
from core.template_registry import builder_entries
from db import idempotency
from tools.registry import resolve_action_fn

router = APIRouter()


@router.post("/actions/execute", response_model=ActionExecutionResult)
async def execute_action(
    request: ActionExecutionRequest,
    credentials: dict = Depends(get_action_credentials),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
):
    # 카탈로그 ACTION 항목의 도구만 실행한다 — _TOOL_MAP 전체를 열면 gmail(smtp_host)·builtin:web_search(플랫폼 키)도 열린다.
    allowed = {t["tool_key"] for t in builder_entries() if t["node_type"] == "ACTION"}
    fn = resolve_action_fn(request.toolKey) if request.toolKey in allowed else None
    if fn is None:
        # 멱등 레코드를 만들기 전에 거른다 — claim 뒤에 던지면 IN_PROGRESS가 남아 그 키의 재시도가 막힌다.
        raise CodedHTTPException(ErrorCode.UNKNOWN_TOOL)

    claimed, cached = await idempotency.claim(x_idempotency_key, ActionExecutionResult)
    if cached is not None:
        return cached

    try:
        result = await run_action(
            fn,
            request,
            credentials["user_id"],
            google_access_token=credentials["google_access_token"],
            notion_token=credentials["notion_token"],
            github_token=credentials["github_token"],
        )
    except BaseException:
        # 취소(CancelledError)로 빠져도 IN_PROGRESS가 남으면 재시도가 막힌다. shield 이유는 api/routes/execute.py 참고.
        if claimed:
            await asyncio.shield(idempotency.release(x_idempotency_key))
        raise

    if claimed:
        if result.success:
            await idempotency.complete(x_idempotency_key, result)
        else:
            await idempotency.release(x_idempotency_key)
    return result
