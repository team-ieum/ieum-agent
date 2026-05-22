import os
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.generate.orchestrator import ORCHESTRATOR_INSTRUCTION
from agents.generate.sub.planner_agent import build_planner_agent
from agents.generate.sub.builder_agent import build_builder_agent

_GENERATE_USER_ID = "generate_user"


async def run_generate_agent(
    prompt: str,
    model: str,
    provider: str,
    api_key: str,
    env_key: str | None,
) -> str:
    """GenerateOrchestratorAgent를 실행하여 워크플로우 JSON 문자열을 반환한다."""
    prev_value = os.environ.get(env_key) if env_key else None
    try:
        if env_key:
            os.environ[env_key] = api_key

        planner_agent = build_planner_agent(model)
        builder_agent = build_builder_agent(model)

        orchestrator = LlmAgent(
            name="generate_orchestrator",
            model=model,
            instruction=ORCHESTRATOR_INSTRUCTION + (
                f"\n\n## 현재 요청 컨텍스트\n"
                f"- provider: {provider.upper()}\n"
                f"  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정한다)"
            ),
            tools=[
                AgentTool(agent=planner_agent),
                AgentTool(agent=builder_agent),
            ],
        )

        session_service = InMemorySessionService()
        runner = Runner(
            agent=orchestrator,
            app_name="ieum-agent",
            session_service=session_service,
        )
        session = await session_service.create_session(
            app_name="ieum-agent",
            user_id=_GENERATE_USER_ID
        )
        message = types.Content(
            role="user",
            parts=[
                types.Part(
                    text=prompt
                )
            ]
        )

        output_parts = []
        async for event in runner.run_async(
                user_id=_GENERATE_USER_ID,
                session_id=session.id,
                new_message=message
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
