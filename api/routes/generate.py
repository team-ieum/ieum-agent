import logging

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.credential import get_llm_credentials
from api.schemas.generate_workflow import GenerateWorkflowRequest, GenerateWorkflowResponse
from common.error_code import ErrorCode
from core.workflow_generator import generate_workflow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-workflow", response_model=GenerateWorkflowResponse)
async def generate_workflow_endpoint(
    request: GenerateWorkflowRequest,
    credentials: dict = Depends(get_llm_credentials),
):
    try:
        return await generate_workflow(
            prompt=request.prompt,
            provider=credentials["provider"],
            api_key=credentials["api_key"],
        )
    except ValueError:
        # LLM 빈 응답 또는 JSON 파싱 실패 — 내부 메시지 노출 없이 일반화된 메시지 반환
        # 502: upstream(LLM)이 잘못된 응답을 반환한 일시적 실패
        raise HTTPException(status_code=ErrorCode.WORKFLOW_PARSE_FAILED.status_code,
                            detail=ErrorCode.WORKFLOW_PARSE_FAILED.message)
    except Exception as e:
        logger.error("워크플로우 생성 중 예상치 못한 오류: %s", str(e), exc_info=True)
        raise HTTPException(status_code=ErrorCode.WORKFLOW_GENERATION_FAILED.status_code,
                            detail=ErrorCode.WORKFLOW_GENERATION_FAILED.message)
