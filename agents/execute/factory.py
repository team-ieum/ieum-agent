import contextlib
import os
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.genai import types

from core.config import settings

from agents.execute.main_agent import MAIN_INSTRUCTION
from agents.execute.sub.web_agent import build_web_agent
from agents.execute.sub.notion_agent import build_notion_agent
from agents.execute.sub.google_agent import build_google_agent
from agents.execute.sub.github_agent import build_github_agent
from agents.execute.sub.communication_agent import build_communication_agent
from agents.execute.sub.transform_agent import build_transform_agent
from agents.execute.sub.mcp_agent import build_mcp_agent
from agents.base import _bind_workflow_context, _bind_google_token, _bind_notion_token
from core.custom_gemini import CustomGemini
from core.config import get_current_time_info
from db.session_service import MongoSessionService
from tools import get_tools_for_request
from api.schemas.request import AgentNodeRequest
from google.adk.sessions import BaseSessionService, InMemorySessionService


# 노드 request.tools의 webhook 도구(slack/discord) → 실제 함수명 매핑.
# comm_agent의 webhook 도구에 webhook_url을 바인딩하기 위해 config를 추출한다.
_WEBHOOK_FN_BY_TOOL = {"slack": "send_slack_message", "discord": "send_discord_webhook"}


class ToolNotCalledError(RuntimeError):
    """노드가 도구를 명시했으나 실행 중 단 한 번도 도구를 호출하지 않았을 때 발생한다.
    LLM이 도구를 쓰지 않고 자연어로 '못 했다'고 답해도 노드가 성공으로 집계되던
    조용한 실패(silent failure)를 방지한다."""
    pass


def _assert_tool_called(tools: list | None, tool_call_count: int) -> None:
    """request.tools에 실행 도구가 명시됐는데 호출이 전무하면 실패로 처리한다.
    mcp_servers나 sub-agent 위임은 function_call로 잡히므로 count에 포함되며,
    여기서는 '명시된 도구가 있는데 아무것도 호출되지 않은' 경우만 차단한다."""
    if tools and tool_call_count == 0:
        raise ToolNotCalledError(
            f"노드에 도구가 {len(tools)}개 지정됐으나 실행 중 도구가 한 번도 호출되지 않았습니다."
        )


def _extract_webhook_configs(tools: list | None) -> dict:
    """request.tools에서 slack/discord의 config(webhook_url 포함)를 함수명 키로 추출한다."""
    import logging
    _log = logging.getLogger(__name__)
    out: dict = {}
    for t in (tools or []):
        if not isinstance(t, dict):
            continue
        fn = _WEBHOOK_FN_BY_TOOL.get(t.get("name"))
        cfg = t.get("config")
        if fn and isinstance(cfg, dict):
            out[fn] = cfg
            _log.warning(
                "[webhook-debug] agent webhook config 추출 — fn: %s, config keys: %s, webhook_url 존재: %s",
                fn, list(cfg.keys()), bool(cfg.get("webhook_url")),
            )
    # webhook 도구가 실제로 명시됐는데 config 추출이 비었을 때만 경고한다.
    # (webhook 없는 일반 노드에서 매번 경고 로그를 남겨 로그가 오염되는 것을 방지)
    has_webhook_tool = any(
        isinstance(t, dict) and t.get("name") in _WEBHOOK_FN_BY_TOOL for t in (tools or [])
    )
    if not out and has_webhook_tool:
        _log.warning("[webhook-debug] agent webhook config 없음 — tools: %s",
                     [t.get("name") if isinstance(t, dict) else t for t in (tools or [])])
    return out


async def _safe_delete_session(session_service: BaseSessionService, user_id: str, session_id: str) -> None:
    """단발성 execute 세션을 실행 종료 후 폐기한다.
    각 execute 호출은 고유 세션을 새로 생성하므로 재사용되지 않으며, 삭제하지 않으면
    agent_sessions 도큐먼트가 무한 누적된다. 세션 정리 실패가 실행 결과를 막지 않도록 예외는 무시한다."""
    try:
        await session_service.delete_session(
            app_name="ieum-agent", user_id=user_id, session_id=session_id
        )
    except Exception:
        pass


async def run_simple_agent(
    model: str,
    request: AgentNodeRequest,
    api_key: str,
    env_key: str | None,
    user_id: str,
    session_service: BaseSessionService | None = None,
) -> tuple[str, int, int, int]:
    """simple 타입: 단일 LlmAgent로 실행. 도구 없이 빠른 LLM 호출."""
    prev_value = None
    cleanup_session_id = None
    is_gemini = (env_key == "GOOGLE_API_KEY") or (not env_key and "gemini" in model.lower())

    # Gemini가 아닌 경우에만 os.environ 조작 (Lock 대상)
    if env_key and not is_gemini:
        prev_value = os.environ.get(env_key)
        os.environ[env_key] = api_key

    try:
        builtin_tools = get_tools_for_request(request.tools or [])
        builtin_tools = _bind_workflow_context(builtin_tools, request.workflowContext or {})

        # Gemini 모델인 경우 CustomGemini를 사용하여 API Key를 직접 주입
        model_param = CustomGemini(model=model, api_key=api_key) if is_gemini else model

        agent = LlmAgent(
            name="ieum_agent",
            model=model_param,
            instruction=(request.systemMessage or "You are a helpful assistant.") + get_current_time_info(),
            tools=builtin_tools,
        )

        if session_service is None:
            session_service = MongoSessionService()
        runner = Runner(
            agent=agent,
            app_name="ieum-agent",
            session_service=session_service
        )
        session = await session_service.create_session(
            app_name="ieum-agent",
            user_id=user_id
        )
        cleanup_session_id = session.id
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
                new_message=message,
                run_config=RunConfig(max_llm_calls=settings.AGENT_MAX_LLM_CALLS)):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        output_parts.append(part.text)
            if hasattr(event, "usage_metadata") and event.usage_metadata:
                total_input += event.usage_metadata.prompt_token_count or 0
                total_output += event.usage_metadata.candidates_token_count or 0
                total_count += event.usage_metadata.total_token_count or 0

        return "\n".join(output_parts), total_input, total_output, total_count

    finally:
        if cleanup_session_id is not None:
            await _safe_delete_session(session_service, user_id, cleanup_session_id)
        if env_key and not is_gemini:
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
    github_token: str | None = None,
    session_service: BaseSessionService | None = None,
    use_single_agent: bool = True,
) -> tuple[str, int, int, int]:
    """react 타입: Main Agent + Sub-Agent 멀티 에이전트 실행. AsyncExitStack으로 MCPToolset 관리."""
    prev_value = None
    is_gemini = (env_key == "GOOGLE_API_KEY") or (not env_key and "gemini" in model.lower())

    if session_service is None:
        session_service = MongoSessionService()

    # Gemini가 아닌 경우에만 os.environ 조작
    if env_key and not is_gemini:
        prev_value = os.environ.get(env_key)
        os.environ[env_key] = api_key

    try:
        active_tokens = [t for t in [google_access_token, notion_token, github_token] if t]
        has_custom_mcp = bool(request.mcp_servers)
        webhook_configs = _extract_webhook_configs(request.tools)

        model_param = CustomGemini(model=model, api_key=api_key) if is_gemini else model

        # [최적화] 외부 연동 크레덴셜이 1개 이하이고 커스텀 MCP가 정의되지 않은 경우
        # 메인-서브 멀티에이전트 오케스트레이션을 우회하고 단일 ReAct Agent로 다이렉트 실행하여 Latency 감소
        # 단, 테스트 환경(use_single_agent가 False인 경우)에는 기존 멀티에이전트 흐름을 유지합니다.
        if len(active_tokens) <= 1 and not has_custom_mcp and use_single_agent:
            async with contextlib.AsyncExitStack() as stack:
                builtin_tools = get_tools_for_request(request.tools or [])
                if google_access_token:
                    builtin_tools = _bind_google_token(builtin_tools, google_access_token)
                if notion_token:
                    builtin_tools = _bind_notion_token(builtin_tools, notion_token)
                builtin_tools = _bind_workflow_context(builtin_tools, request.workflowContext or {})

                mcp_tools = []
                if notion_token:
                    notion_agent, _ = await build_notion_agent(model_param, notion_token, stack)
                    mcp_tools.extend(notion_agent.tools)
                elif google_access_token:
                    google_agent, _ = await build_google_agent(model_param, google_access_token, stack)
                    mcp_tools.extend(google_agent.tools)
                elif github_token:
                    github_agent, _ = await build_github_agent(model_param, github_token, stack)
                    mcp_tools.extend(github_agent.tools)

                web_agent, _ = await build_web_agent(model_param)
                comm_agent, _ = await build_communication_agent(model_param, webhook_configs)
                transform_agent, _ = await build_transform_agent(model_param)

                raw_direct_tools = [
                    *builtin_tools,
                    *mcp_tools,
                    *(web_agent.tools or []),
                    *(comm_agent.tools or []),
                    *(transform_agent.tools or []),
                ]
                seen_names = set()
                direct_tools = []
                for t in raw_direct_tools:
                    if t.name not in seen_names:
                        seen_names.add(t.name)
                        direct_tools.append(t)

                single_agent = LlmAgent(
                    name="ieum_single_agent",
                    model=model_param,
                    instruction=(
                        "당신은 IEUM 워크플로우 실행 에이전트입니다. 주어진 도구들을 사용하여 사용자의 요청을 직접 처리하세요.\n"
                        f"\n## 사용자 지시\n{request.systemMessage or ''}"
                        + get_current_time_info()
                    ),
                    tools=direct_tools,
                )

                runner = Runner(
                    agent=single_agent,
                    app_name="ieum-agent",
                    session_service=session_service
                )
                session = await session_service.create_session(
                    app_name="ieum-agent",
                    user_id=user_id
                )
                stack.push_async_callback(_safe_delete_session, session_service, user_id, session.id)
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
                tool_call_count = 0

                async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session.id,
                        new_message=message,
                        run_config=RunConfig(max_llm_calls=settings.AGENT_MAX_LLM_CALLS)
                ):
                    tool_call_count += len(event.get_function_calls() or [])
                    if event.is_final_response() and event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                output_parts.append(part.text)
                    if hasattr(event, "usage_metadata") and event.usage_metadata:
                        um = event.usage_metadata
                        total_input += um.prompt_token_count or 0
                        total_output += um.candidates_token_count or 0
                        total_count += um.total_token_count or 0

                _assert_tool_called(request.tools, tool_call_count)
                return "\n".join(output_parts), total_input, total_output, total_count

        # [기본 흐름] 복수 크레덴셜 또는 커스텀 MCP가 있는 경우 오케스트레이터(Main) + 전문 서브에이전트 구조로 실행
        async with contextlib.AsyncExitStack() as stack:
            web_agent, _ = await build_web_agent(model_param)
            comm_agent, _ = await build_communication_agent(model_param, webhook_configs)
            transform_agent, _ = await build_transform_agent(model_param)

            sub_agent_tools = [
                AgentTool(agent=web_agent),
                AgentTool(agent=comm_agent),
                AgentTool(agent=transform_agent),
            ]

            if notion_token:
                notion_agent, _ = await build_notion_agent(model_param, notion_token, stack)
                sub_agent_tools.append(AgentTool(agent=notion_agent))

            if google_access_token:
                google_agent, _ = await build_google_agent(model_param, google_access_token, stack)
                sub_agent_tools.append(AgentTool(agent=google_agent))

            if github_token:
                github_agent, _ = await build_github_agent(model_param, github_token, stack)
                sub_agent_tools.append(AgentTool(agent=github_agent))

            mcp_server_configs = [s.model_dump() for s in (request.mcp_servers or [])]
            if mcp_server_configs:
                mcp_agent, _ = await build_mcp_agent(model_param, mcp_server_configs, stack)
                sub_agent_tools.append(AgentTool(agent=mcp_agent))

            # builtin 도구 바인딩
            builtin_tools = get_tools_for_request(request.tools or [])
            if google_access_token:
                builtin_tools = _bind_google_token(builtin_tools, google_access_token)
            if notion_token:
                builtin_tools = _bind_notion_token(builtin_tools, notion_token)
            builtin_tools = _bind_workflow_context(builtin_tools, request.workflowContext or {})

            # Main Agent 구성
            main_agent = LlmAgent(
                name="ieum_main_agent",
                model=model_param,
                instruction=MAIN_INSTRUCTION + (
                    f"\n\n## 사용자 지시\n{request.systemMessage}" if request.systemMessage else ""
                ) + get_current_time_info(),
                tools=[
                    *sub_agent_tools,
                    *builtin_tools,
                ],
            )

            runner = Runner(
                agent=main_agent,
                app_name="ieum-agent",
                session_service=session_service)
            session = await session_service.create_session(
                app_name="ieum-agent",
                user_id=user_id
            )
            stack.push_async_callback(_safe_delete_session, session_service, user_id, session.id)
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
            tool_call_count = 0

            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=message,
                    run_config=RunConfig(max_llm_calls=settings.AGENT_MAX_LLM_CALLS)
            ):
                tool_call_count += len(event.get_function_calls() or [])
                if event.is_final_response() and event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            output_parts.append(part.text)
                if hasattr(event, "usage_metadata") and event.usage_metadata:
                    um = event.usage_metadata
                    total_input += um.prompt_token_count or 0
                    total_output += um.candidates_token_count or 0
                    total_count += um.total_token_count or 0

            _assert_tool_called(request.tools, tool_call_count)
            return "\n".join(output_parts), total_input, total_output, total_count

    finally:
        if env_key and not is_gemini:
            if prev_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prev_value
