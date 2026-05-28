import os
import json
import logging
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.generate.sub.planner_agent import build_planner_agent
from agents.generate.sub.builder_agent import build_builder_agent
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.validators.plan_validator import PlanValidator

logger = logging.getLogger(__name__)

_GENERATE_USER_ID = "generate_user"


async def _run_single_agent(agent: LlmAgent, prompt_text: str, user_id: str) -> str:
    """단일 LlmAgent를 실행하여 최종 텍스트 결과를 반환한다."""
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name="ieum-agent",
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name="ieum-agent",
        user_id=user_id
    )
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                text=prompt_text
            )
        ]
    )

    output_parts = []
    async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=message
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    output_parts.append(part.text)

    return "\n".join(output_parts) if output_parts else ""


def _parse_and_validate_plan(raw_plan: str) -> WorkflowPlanSchema:
    cleaned = raw_plan.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.rstrip().endswith("```"):
        cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
    cleaned = cleaned.strip()

    data = json.loads(cleaned)
    plan = WorkflowPlanSchema(**data)
    PlanValidator.validate(plan)
    return plan


async def run_generate_agent(
    prompt: str,
    model: str,
    provider: str,
    api_key: str,
    env_key: str | None,
) -> str:
    """Orchestrator 없이 파이썬 코드로 Planner(Plan생성/검증) ➡️ Builder를 직접 순차 실행한다."""
    prev_value = os.environ.get(env_key) if env_key else None
    try:
        if env_key:
            os.environ[env_key] = api_key

        planner_agent = build_planner_agent(model, prompt, provider)

        # 1. 계획(Plan) 생성 1차 시도
        plan_raw = await _run_single_agent(planner_agent, prompt, _GENERATE_USER_ID)

        try:
            plan = _parse_and_validate_plan(plan_raw)
        except Exception as first_err:
            logger.warning("1차 계획(Plan) 검증 실패: %s. 1회 자가 교정을 시도합니다.", str(first_err))
            feedback = (
                f"당신이 이전에 작성한 계획(Plan)에 설계상 결함이 발견되어 검증에 실패했습니다.\n"
                f"오류 피드백을 수용하여 사용자 요청에 부합하는 올바른 JSON Plan을 재생성하십시오.\n\n"
                f"## 오류 피드백:\n{str(first_err)}\n\n"
                f"## 사용자 원래 요청:\n{prompt}"
            )
            plan_raw = await _run_single_agent(planner_agent, feedback, _GENERATE_USER_ID)
            plan = _parse_and_validate_plan(plan_raw)

        logger.info("성공적으로 워크플로우 계획(Plan)이 검증 통과했습니다. Justification: %s", plan.justification)

        # 2. 최종 워크플로우 빌드
        builder_agent = build_builder_agent(model, prompt, provider)
        builder_prompt = (
            f"사용자 원래 요청: {prompt}\n\n"
            f"현재 요청 컨텍스트:\n- provider: {provider.upper()}\n"
            f"  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정해야 합니다)\n\n"
            f"수립된 워크플로우 계획 (반드시 준수할 것):\n{plan.model_dump_json(indent=2)}\n\n"
            f"위 계획의 구조(노드 수, 연결 흐름)를 철저히 준수하여 최종 워크플로우 JSON을 생성하십시오."
        )

        workflow_raw = await _run_single_agent(builder_agent, builder_prompt, _GENERATE_USER_ID)
        return workflow_raw

    finally:
        if env_key:
            if prev_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prev_value
