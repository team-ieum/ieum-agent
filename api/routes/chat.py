import logging

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.credential import get_llm_credentials
from api.schemas.chat import ChatRequest, ChatResponse
from common.error_code import ErrorCode
from core.workflow_chat import chat_workflow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    credentials: dict = Depends(get_llm_credentials),
):
    try:
        return await chat_workflow(
            prompt=request.prompt,
            provider=credentials["provider"],
            api_key=credentials["api_key"],
            user_id=credentials["user_id"],
            available_integrations=[a.model_dump() for a in request.availableIntegrations],
            unavailable_integrations=[u.model_dump() for u in request.unavailableIntegrations],
            current_nodes=[n.model_dump() for n in request.currentNodes] if request.currentNodes else None,
            current_edges=[e.model_dump() for e in request.currentEdges] if request.currentEdges else None,
            notion_token=credentials.get("notion_token"),
            github_token=credentials.get("github_token"),
            google_access_token=credentials.get("google_access_token"),
            mcp_servers=[s.model_dump() for s in request.mcpServers] if request.mcpServers else None,
        )
    except ValueError:
        raise HTTPException(
            status_code=ErrorCode.CHAT_PARSE_FAILED.status_code,
            detail=ErrorCode.CHAT_PARSE_FAILED.message,
        )
    except Exception as e:
        logger.error("채팅 처리 중 예상치 못한 오류: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=ErrorCode.CHAT_EXECUTION_FAILED.status_code,
            detail=ErrorCode.CHAT_EXECUTION_FAILED.message,
        )
