import logging

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.credential import get_llm_credentials
from api.schemas.modify_workflow import ModifyWorkflowRequest, ModifyWorkflowResponse
from common.error_code import ErrorCode
from core.workflow_modifier import modify_workflow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/modify-workflow", response_model=ModifyWorkflowResponse)
async def modify_workflow_endpoint(
    request: ModifyWorkflowRequest,
    credentials: dict = Depends(get_llm_credentials),
):
    try:
        return await modify_workflow(
            prompt=request.prompt,
            current_nodes=[n.model_dump() for n in request.currentNodes],
            current_edges=[e.model_dump() for e in request.currentEdges],
            provider=credentials["provider"],
            api_key=credentials["api_key"],
        )
    except ValueError:
        raise HTTPException(status_code=ErrorCode.WORKFLOW_MODIFY_PARSE_FAILED.status_code,
                            detail=ErrorCode.WORKFLOW_MODIFY_PARSE_FAILED.message)
    except Exception as e:
        logger.error("워크플로우 수정 중 예상치 못한 오류: %s", str(e), exc_info=True)
        raise HTTPException(status_code=ErrorCode.WORKFLOW_MODIFY_FAILED.status_code,
                            detail=ErrorCode.WORKFLOW_MODIFY_FAILED.message)
