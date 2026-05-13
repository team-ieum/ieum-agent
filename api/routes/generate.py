from fastapi import APIRouter, Depends, HTTPException

from api.middleware.credential import get_llm_credentials
from api.schemas.generate_workflow import GenerateWorkflowRequest, GenerateWorkflowResponse
from core.workflow_generator import generate_workflow

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
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
