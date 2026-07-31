import asyncio

from fastapi import APIRouter, Depends, Header
from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from api.middleware.credential import get_llm_credentials
from core.agent import run_agent
from db import idempotency

router = APIRouter()

@router.post("/execute", response_model=AgentExecutionResult)
async def execute(
    request: AgentNodeRequest,
    credentials: dict = Depends(get_llm_credentials),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
):
    # BE는 재시도가 켜진 노드에만 이 헤더를 붙인다. 헤더가 없으면 기존 동작 그대로다.
    claimed, cached = await idempotency.claim(x_idempotency_key)
    if cached is not None:
        return cached

    try:
        result = await run_agent(
            request,
            credentials["provider"],
            credentials["api_key"],
            credentials["user_id"],
            user_role=credentials.get("user_role"),
            key_mode=credentials.get("key_mode"),
            google_access_token=credentials.get("google_access_token"),
            notion_token=credentials.get("notion_token"),
            github_token=credentials.get("github_token"),
        )
    except BaseException:
        # 예외로 빠져도 IN_PROGRESS 레코드가 남으면 그 노드의 재시도가 전부 막힌다.
        # Exception이 아니라 BaseException을 잡는다 — run_agent()는 모든 Exception을
        # 내부에서 처리하고 AgentExecutionResult를 반환하므로, 여기 도달하는 실질적 경로는
        # asyncio.CancelledError(BaseException 직속)뿐이다. 그 원천은 주로 프로세스 종료
        # (uvicorn SIGTERM) 계열이다 — 비스트리밍 POST는 클라이언트가 끊어도 자동 취소되지 않는다.
        # shield로 감싸는 이유: 취소된 태스크에서 그냥 await하면 재취소가 걸릴 때 release가
        # 중간에 끊겨 레코드가 그대로 남는다(실측 확인). shield는 내부 실행을 끝까지 보장한다.
        if claimed:
            await asyncio.shield(idempotency.release(x_idempotency_key))
        raise

    if claimed:
        if result.success:
            await idempotency.complete(x_idempotency_key, result)
        else:
            await idempotency.release(x_idempotency_key)
    return result
