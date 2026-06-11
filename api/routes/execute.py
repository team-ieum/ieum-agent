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
        user_role=credentials.get("user_role"),
        google_access_token=credentials.get("google_access_token"),
        notion_token=credentials.get("notion_token"),
        github_token=credentials.get("github_token"),
    )
