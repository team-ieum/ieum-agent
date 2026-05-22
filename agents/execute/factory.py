import contextlib
import os
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.execute.main_agent import MAIN_INSTRUCTION
from agents.execute.sub.web_agent import build_web_agent
from agents.execute.sub.notion_agent import build_notion_agent
from agents.execute.sub.google_agent import build_google_agent
from agents.execute.sub.github_agent import build_github_agent
from agents.execute.sub.communication_agent import build_communication_agent
from agents.execute.sub.mcp_agent import build_mcp_agent
from agents.base import _bind_workflow_context
from tools import get_tools_for_request
from api.schemas.request import AgentNodeRequest


async def run_simple_agent(
    model: str,
    request: AgentNodeRequest,
    api_key: str,
    env_key: str | None,
    user_id: str,
) -> tuple[str, int, int, int]:
    """simple 타입: 단일 LlmAgent로 실행. 도구 없이 빠른 LLM 호출."""
    prev_value = os.environ.get(env_key) if env_key else None
    try:
        if env_key:
            os.environ[env_key] = api_key

        agent = LlmAgent(
            name="ieum_agent",
            model=model,
            instruction=request.systemMessage or "You are a helpful assistant.",
            tools=[],
        )

        session_service = InMemorySessionService()
        runner = Runner(
            agent=agent,
            app_name="ieum-agent",
            session_service=session_service
        )
        session = await session_service.create_session(
            app_name="ieum-agent",
            user_id=user_id
        )
        message = types.Content(
            role="user",
            parts=[
                types.Part(
                    text=request.renderedPrompt
                )
            ]
        )

        output_parts = []
        total_input = total_output = total_count = 0

        async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=message):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        output_parts.append(part.text)
            if hasattr(event, "usage_metadata") and event.usage_metadata:
                total_input = event.usage_metadata.prompt_token_count or 0
                total_output = event.usage_metadata.candidates_token_count or 0
                total_count = event.usage_metadata.total_token_count or 0

        return "\n".join(output_parts), total_input, total_output, total_count

    finally:
        if env_key:
            if prev_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prev_value


async def run_react_agent(
    model: str,
    request: AgentNodeRequest,
    api_key: str,
    env_key: str | None,
    user_id: str,
    google_access_token: str | None = None,
    notion_token: str | None = None,
    github_pat: str | None = None,
) -> tuple[str, int, int, int]:
    """react 타입: Main Agent + Sub-Agent 멀티 에이전트 실행. AsyncExitStack으로 MCPToolset 관리."""
    prev_value = os.environ.get(env_key) if env_key else None
    try:
        if env_key:
            os.environ[env_key] = api_key

        async with contextlib.AsyncExitStack() as stack:
            # Sub-agent 빌드 (MCPToolset 라이프사이클을 stack으로 관리)
            web_agent, _ = await build_web_agent(model)
            notion_agent, _ = await build_notion_agent(model, notion_token, stack)
            google_agent, _ = await build_google_agent(model, google_access_token, stack)
            github_agent, _ = await build_github_agent(model, github_pat, stack)
            comm_agent, _ = await build_communication_agent(model)
            mcp_server_configs = [
                s.model_dump() for s in (request.mcp_servers or [])
            ]
            mcp_agent, _ = await build_mcp_agent(model, mcp_server_configs, stack)

            # builtin 도구 (workflow_context 등) 바인딩
            builtin_tools = get_tools_for_request(request.tools or [])
            builtin_tools = _bind_workflow_context(builtin_tools, request.workflowContext or {})

            # Main Agent 구성
            main_agent = LlmAgent(
                name="ieum_main_agent",
                model=model,
                instruction=MAIN_INSTRUCTION + (
                    f"\n\n## 사용자 지시\n{request.systemMessage}" if request.systemMessage else ""
                ),
                tools=[
                    AgentTool(agent=web_agent),
                    AgentTool(agent=notion_agent),
                    AgentTool(agent=google_agent),
                    AgentTool(agent=github_agent),
                    AgentTool(agent=comm_agent),
                    AgentTool(agent=mcp_agent),
                    *builtin_tools,
                ],
            )

            session_service = InMemorySessionService()
            runner = Runner(
                agent=main_agent,
                app_name="ieum-agent",
                session_service=session_service)
            session = await session_service.create_session(
                app_name="ieum-agent",
                user_id=user_id
            )
            message = types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=request.renderedPrompt
                    )
                ]
            )

            output_parts = []
            total_input = total_output = total_count = 0

            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=message
            ):
                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            output_parts.append(part.text)
                if hasattr(event, "usage_metadata") and event.usage_metadata:
                    um = event.usage_metadata
                    total_input += um.prompt_token_count or 0
                    total_output += um.candidates_token_count or 0
                    total_count += um.total_token_count or 0

            return "\n".join(output_parts), total_input, total_output, total_count

    finally:
        if env_key:
            if prev_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prev_value
