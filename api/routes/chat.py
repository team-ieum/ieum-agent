import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.middleware.credential import get_llm_credentials
from api.schemas.chat import ChatRequest, ChatResponse
from common.error_code import ErrorCode
from common.exception import llm_http_exception
from core.workflow_chat import chat_workflow, chat_workflow_stream

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
    request: ChatRequest,
    credentials: dict = Depends(get_llm_credentials),
):
    """워크플로우 설계 진행 단계를 SSE로 스트리밍한다.

    이벤트 순서: stage(designing → reviewing) → done(완성 ChatResponse). 실행 중 예외는
    error 이벤트로 전달된다. 블로킹 /v1/chat과 동일한 최종 결과를 만들되 진행 상황을 실시간 전달한다.
    """
    kwargs = _build_chat_kwargs(request, credentials)

    async def event_publisher():
        async for event, data in chat_workflow_stream(**kwargs):
            data_str = data.model_dump_json() if event == "done" else json.dumps(data, ensure_ascii=False)
            yield {"event": event, "data": data_str}

    return EventSourceResponse(event_publisher())
