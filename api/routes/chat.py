import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.middleware.credential import get_llm_credentials
from api.schemas.chat import BuildChatRequest, ChatRequest, ChatResponse
from common.error_code import ErrorCode
from common.exception import llm_http_exception
from core.builder.engine import build_turn_stream
from core.workflow_chat import chat_workflow

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_chat_kwargs(request: ChatRequest, credentials: dict) -> dict:
    """ChatRequest와 자격증명을 chat_workflow 호출 인자로 변환한다.

    블로킹(/v1/chat)과 스트리밍(/v1/chat/stream) 엔드포인트가 동일한 인자 매핑을 공유한다."""
    return dict(
        prompt=request.prompt,
        provider=credentials["provider"],
        api_key=credentials["api_key"],
        user_id=credentials["user_id"],
        user_role=credentials.get("user_role"),
        key_mode=credentials.get("key_mode"),
        workflow_id=request.workflowId,
        available_integrations=[a.model_dump() for a in request.availableIntegrations],
        unavailable_integrations=[u.model_dump() for u in request.unavailableIntegrations],
        current_nodes=[n.model_dump() for n in request.currentNodes] if request.currentNodes else None,
        current_edges=[e.model_dump() for e in request.currentEdges] if request.currentEdges else None,
        notion_token=credentials.get("notion_token"),
        github_token=credentials.get("github_token"),
        google_access_token=credentials.get("google_access_token"),
        mcp_servers=[s.model_dump() for s in request.mcpServers] if request.mcpServers else None,
        available_mcp_servers=[m.model_dump() for m in request.availableMcpServers] if request.availableMcpServers else None,
        available_webhooks=[w.model_dump() for w in request.availableWebhooks] if request.availableWebhooks else None,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    credentials: dict = Depends(get_llm_credentials),
):
    try:
        return await chat_workflow(**_build_chat_kwargs(request, credentials))
    except ValueError:
        logger.exception("[chat-debug] chat_workflow ValueError — 실제 원인 추적")
        raise HTTPException(
            status_code=ErrorCode.CHAT_PARSE_FAILED.status_code,
            detail=ErrorCode.CHAT_PARSE_FAILED.message,
        )
    except Exception as e:
        logger.error("채팅 처리 중 예상치 못한 오류: %s", str(e), exc_info=True)
        raise llm_http_exception(e, ErrorCode.CHAT_EXECUTION_FAILED)


@router.post("/chat/stream")
async def chat_stream_endpoint(
    request: BuildChatRequest,
    credentials: dict = Depends(get_llm_credentials),
    x_delegation_token: str | None = Header(None, alias="X-Delegation-Token"),
):
    """대화형 노드 단위 생성 한 턴을 SSE로 낸다(spec 2026-10-28). 이벤트: stage(designing) → done(BuildChatResponse)
    또는 error. 블로킹 /v1/chat은 옛 엔진 그대로다(새 엔진은 WebSocket 스트림 전용)."""
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
