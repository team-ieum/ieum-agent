import logging
import time
from datetime import datetime, timezone

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult, UsageRecord
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from db.mongodb import execution_logs
from google.adk.sessions import BaseSessionService
from agents.execute.factory import run_simple_agent, run_react_agent

logger = logging.getLogger(__name__)


async def save_execution_log(
    user_id: str,
    node_id: str,
    workflow_execution_id: str | None,
    provider: str,
    model: str,
    agent_type: str,
    result: AgentExecutionResult,
    duration_ms: int,
):
    await execution_logs.insert_one({
        "userId": user_id,
        "nodeId": node_id,
        "workflowExecutionId": workflow_execution_id,
        "provider": provider,
        "model": model,
        "agentType": agent_type,
        "status": result.status,
        "success": result.success,
        "output": result.output,
        "errorMessage": result.errorMessage,
        "toolCalls": [tc.model_dump() for tc in result.toolCalls] if result.toolCalls else [],
        "usage": result.usage.model_dump() if result.usage else None,
        "durationMs": duration_ms,
        "createdAt": datetime.now(timezone.utc),
    })


async def run_agent(
    request: AgentNodeRequest,
    provider: str,
    api_key: str,
    user_id: str,
    google_access_token: str | None = None,
    notion_token: str | None = None,
    github_token: str | None = None,
    session_service: BaseSessionService | None = None,
) -> AgentExecutionResult:
    start = time.monotonic()
    result = AgentExecutionResult(success=False)

    env_key = resolve_env_key(provider)
    # Gemini인 경우 os.environ을 통한 임시 주입 대신 CustomGemini를 통해 API Key를 직접 주입하므로 Lock을 잡지 않습니다.
    lock = get_env_lock(env_key) if (env_key and provider.upper() != "GEMINI") else None

    model = resolve_model(provider, request.model)

    try:
        async def _execute() -> tuple[str, int, int, int]:
            if request.agentType == "react":
                return await run_react_agent(
                    model=model,
                    request=request,
                    api_key=api_key,
                    env_key=env_key,
                    user_id=user_id,
                    google_access_token=google_access_token,
                    notion_token=notion_token,
                    github_token=github_token,
                    session_service=session_service,
                )
            else:
                return await run_simple_agent(
                    model=model,
                    request=request,
                    api_key=api_key,
                    env_key=env_key,
                    user_id=user_id,
                    session_service=session_service,
                )

        if lock:
            async with lock:
                output, input_tokens, output_tokens, total_tokens = await _execute()
        else:
            output, input_tokens, output_tokens, total_tokens = await _execute()

        usage = UsageRecord(
            promptTokens=input_tokens,
            completionTokens=output_tokens,
            totalTokens=total_tokens or (input_tokens + output_tokens),
        ) if (input_tokens or output_tokens) else None

        result = AgentExecutionResult(
            success=True,
            status="COMPLETED",
            output=output,
            usage=usage,
        )

    except Exception as e:
        logger.exception("Agent execution failed")
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=ErrorCode.AGENT_EXECUTION_FAILED.message,
        )

    duration_ms = int((time.monotonic() - start) * 1000)

    try:
        await save_execution_log(
            user_id=user_id,
            node_id=request.nodeId,
            workflow_execution_id=request.workflowExecutionId,
            provider=provider,
            model=model,
            agent_type=request.agentType,
            result=result,
            duration_ms=duration_ms,
        )
    except Exception:
        logger.warning("Failed to save execution log for node %s", request.nodeId, exc_info=True)

    return result
