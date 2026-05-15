from fastapi import APIRouter, Depends
from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from api.middleware.credential import get_llm_credentials
from core.agent import run_agent

router = APIRouter()

@router.post("/execute", response_model=AgentExecutionResult)
async def execute(
    request: AgentNodeRequest,
    credentials: dict = Depends(get_llm_credentials)
):
    return await run_agent(
        request,
        credentials["provider"],
        credentials["api_key"],
        credentials["user_id"],
        credentials.get("google_access_token"),
    )
