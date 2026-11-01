import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.middleware.credential import get_llm_credentials
from api.schemas.chat import BuildChatRequest
from core.builder.engine import build_turn_stream

router = APIRouter()


@router.post("/chat")
async def chat_endpoint():
    """REST 블로킹 채팅은 끝났다 — 대화형 생성은 WebSocket 스트림(/v1/chat/stream) 전용이다(spec 2026-10-28 범위 밖)."""
    raise HTTPException(status_code=410, detail="채팅은 워크플로우 화면의 실시간 채팅에서만 쓸 수 있어요.")


@router.post("/chat/stream")
async def chat_stream_endpoint(
    request: BuildChatRequest,
    credentials: dict = Depends(get_llm_credentials),
    x_delegation_token: str | None = Header(None, alias="X-Delegation-Token"),
):
    """대화형 노드 단위 생성 한 턴을 SSE로 낸다(spec 2026-10-28). 이벤트: stage(designing) → done(BuildChatResponse)
    또는 error."""
    kwargs = dict(
        prompt=request.prompt,
        provider=credentials["provider"],
        api_key=credentials["api_key"],
        user_id=credentials["user_id"],
        user_role=credentials.get("user_role"),
        workflow_id=request.workflowId,
        current_nodes=[n.model_dump() for n in request.currentNodes or []],
        current_edges=request.currentEdges or [],
        delegation_token=x_delegation_token,
    )

    async def event_publisher():
        async for event, data in build_turn_stream(**kwargs):
            data_str = data.model_dump_json() if event == "done" else json.dumps(data, ensure_ascii=False)
            yield {"event": event, "data": data_str}

    return EventSourceResponse(event_publisher())
