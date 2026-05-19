import asyncio
import functools
import inspect
import logging
import os
import time
from datetime import datetime, timezone

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from api.schemas.request import AgentNodeRequest
from api.schemas.response import AgentExecutionResult, UsageRecord
from common.error_code import ErrorCode
from core.env_lock import get_env_lock
from core.provider_config import resolve_model, resolve_env_key
from db.mongodb import execution_logs
from tools import get_tools_for_request
from tools.google_sheets import google_sheets_read, google_sheets_write
from tools.google_calendar import google_calendar_create, google_calendar_list
from tools.google_drive import google_drive_read, google_drive_upload
from tools.notion import (
    notion_create_page,
    notion_read_page,
    notion_search,
    notion_update_page,
    notion_append_block,
)
from tools.workflow_context import workflow_context as _workflow_context_fn

logger = logging.getLogger(__name__)

_GOOGLE_TOOL_FUNCTIONS = {
    google_sheets_read,
    google_sheets_write,
    google_calendar_create,
    google_calendar_list,
    google_drive_read,
    google_drive_upload,
}

_NOTION_TOOL_FUNCTIONS = {
    notion_create_page,
    notion_read_page,
    notion_search,
    notion_update_page,
    notion_append_block,
}

_WORKFLOW_CONTEXT_FUNCTIONS = {_workflow_context_fn}


def _get_tool_function(tool):
    return getattr(tool, "func", None) or getattr(tool, "_func", None)


def _get_base_function(fn):
    while isinstance(fn, functools.partial):
        fn = fn.func
    return fn


def _make_partial(fn, **bound_args):
    """fn의 일부 파라미터를 바인딩하고 __signature__에서 해당 파라미터를 제거한 partial을 반환한다."""
    sig = inspect.signature(fn)
    p = functools.partial(fn, **bound_args)
    p.__name__ = fn.__name__
    p.__doc__ = fn.__doc__
    p.__signature__ = sig.replace(
        parameters=[v for k, v in sig.parameters.items() if k not in bound_args]
    )
    return p


def _bind_tool_argument(tool, **bound_args):
    fn = _get_tool_function(tool)
    if fn is None:
        return tool

    bound_fn = _make_partial(fn, **bound_args)
    return FunctionTool(bound_fn)


def _bind_workflow_context(tools: list, context_data: dict) -> list:
    """workflow_context 도구의 workflow_context_data 파라미터를 실제 컨텍스트 데이터로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _WORKFLOW_CONTEXT_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, workflow_context_data=context_data))
        else:
            bound.append(tool)
    return bound


def _bind_google_token(tools: list, google_access_token: str) -> list:
    """Google 도구의 access_token 파라미터를 실제 토큰으로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _GOOGLE_TOOL_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, access_token=google_access_token))
        else:
            bound.append(tool)
    return bound


def _bind_notion_token(tools: list, notion_token: str) -> list:
    """notion_* 도구의 token 파라미터를 실제 Notion Integration Token으로 바인딩한다."""
    bound = []
    for tool in tools:
        fn = _get_tool_function(tool)
        if fn is not None and _get_base_function(fn) in _NOTION_TOOL_FUNCTIONS:
            bound.append(_bind_tool_argument(tool, token=notion_token))
        else:
            bound.append(tool)
    return bound


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
) -> AgentExecutionResult:
    start = time.monotonic()
    result = AgentExecutionResult(success=False)

    env_key = resolve_env_key(provider)
    lock = get_env_lock(env_key) if env_key else None

    model = resolve_model(provider, request.model)

    try:
        async def _execute() -> tuple[str, int, int, int]:
            prev_value = os.environ.get(env_key) if env_key else None
            try:
                if env_key:
                    os.environ[env_key] = api_key

                tools = get_tools_for_request(request.tools or [])
                if google_access_token:
                    tools = _bind_google_token(tools, google_access_token)
                if notion_token:
                    tools = _bind_notion_token(tools, notion_token)
                tools = _bind_workflow_context(tools, request.workflowContext or {})

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
                    user_id=user_id,
                )

                message = types.Content(
                    role="user",
                    parts=[types.Part(text=request.renderedPrompt)],
                )

                output_parts = []
                total_input_tokens = 0
                total_output_tokens = 0
                total_token_count = 0
                is_react = request.agentType == "react"

                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=message,
                ):
                    if event.is_final_response() and event.content:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                output_parts.append(part.text)

                    if hasattr(event, "usage_metadata") and event.usage_metadata:
                        input_count = event.usage_metadata.prompt_token_count or 0
                        output_count = event.usage_metadata.candidates_token_count or 0
                        total_count = event.usage_metadata.total_token_count or 0

                        if is_react:
                            # ReAct: LLM 다회 호출 → 각 호출 단위 누적
                            total_input_tokens += input_count
                            total_output_tokens += output_count
                            total_token_count += total_count
                        else:
                            # Simple: 단일 LLM 호출 → 마지막 값으로 덮어쓰기
                            total_input_tokens = input_count
                            total_output_tokens = output_count
                            total_token_count = total_count

                return "\n".join(output_parts) if output_parts else "", total_input_tokens, total_output_tokens, total_token_count

            finally:
                if env_key:
                    if prev_value is None:
                        os.environ.pop(env_key, None)
                    else:
                        os.environ[env_key] = prev_value

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
