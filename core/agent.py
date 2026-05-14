import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from db.mongodb import execution_logs
from tools import get_tools_for_request

logger = logging.getLogger(__name__)

async def save_execution_log(
    node_id: str,
    workflow_execution_id: str | None,
    provider: str,
    model: str,
    agent_type: str,
    result: AgentExecutionResult,
    duration_ms: int,
):
    await execution_logs.insert_one({
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


async def run_agent(request: AgentNodeRequest, provider: str, api_key: str) -> AgentExecutionResult:
    start = time.monotonic()
    result = AgentExecutionResult(success=False)

    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if env_key else None

    try:
        async def _execute():
            prev_value = os.environ.get(env_key) if env_key else None
            try:
                if env_key:
                    os.environ[env_key] = api_key

                tools = get_tools_for_request(request.tools or [])
                model = resolve_model(provider, request.model)

                agent = LlmAgent(
                    name="ieum_agent",
                    model=model,
                    instruction=request.systemMessage or "You are a helpful assistant.",
                    tools=tools,
                )

                session_service = InMemorySessionService()
                runner = Runner(
                    agent=agent,
                    app_name="ieum-agent",
                    session_service=session_service,
                )

                session = await session_service.create_session(
                    app_name="ieum-agent",
                    user_id="user",
                )

                message = types.Content(
                    role="user",
                    parts=[types.Part(text=request.renderedPrompt)],
                )

                output_parts = []
                async for event in runner.run_async(
                    user_id="user",
                    session_id=session.id,
                    new_message=message,
                ):
                    if event.is_final_response() and event.content:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                output_parts.append(part.text)

                return "\n".join(output_parts) if output_parts else ""

            finally:
                if env_key:
                    if prev_value is None:
                        os.environ.pop(env_key, None)
                    else:
                        os.environ[env_key] = prev_value

        if lock:
            async with lock:
                output = await _execute()
        else:
            output = await _execute()

        result = AgentExecutionResult(success=True, status="COMPLETED", output=output)

    except Exception as e:
        result = AgentExecutionResult(
            success=False,
            status="ERROR",
            errorMessage=ErrorCode.AGENT_EXECUTION_FAILED.message,
        )

    duration_ms = int((time.monotonic() - start) * 1000)

    try:
        await save_execution_log(
            node_id=request.nodeId,
            workflow_execution_id=request.workflowExecutionId,
            provider=provider,
            model=resolve_model(provider, request.model),
            agent_type=request.agentType,
            result=result,
            duration_ms=duration_ms,
        )
    except Exception:
        logger.warning("Failed to save execution log for node %s", request.nodeId, exc_info=True)

    return result
