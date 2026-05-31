import os
import json
import logging
from typing import Callable
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

# Builder가 생성한 워크플로우 JSON을 검증하는 콜백. 검증 실패 시 예외를 던진다.
# (factory가 WorkflowValidator/스키마를 직접 import하면 순환 의존이 생기므로 호출부에서 주입한다.)
ValidateFn = Callable[[str], object]

_MAX_BUILDER_RETRIES = 2


def _build_reflexion_prompt(
    prompt: str, provider: str, plan: WorkflowPlanSchema, broken_json: str, error: str
) -> str:
    """직전에 Builder가 생성한 결함 JSON 원문과 구체적 검증 오류를 함께 제시하여
    Builder가 '재생성'이 아니라 '오류 수정'을 하도록 유도하는 피드백 프롬프트를 만든다."""
    return (
        "당신이 직전에 생성한 워크플로우 JSON이 검증에 실패했습니다.\n"
        "아래 검증 오류를 정확히 수정하여 올바른 워크플로우 JSON을 다시 출력하십시오.\n"
        "전체를 새로 짜지 말고, 결함 부분만 고치는 것을 원칙으로 합니다.\n\n"
        f"## 검증 오류 (반드시 해소할 것):\n{error}\n\n"
        f"## 직전에 당신이 생성한 결함 JSON:\n{broken_json}\n\n"
        f"## 반드시 준수할 워크플로우 계획:\n{plan.model_dump_json(indent=2)}\n\n"
        f"## 요청 컨텍스트:\n- provider: {provider.upper()} "
        f"(모든 AI 노드의 llmProvider는 \"{provider.upper()}\")\n\n"
        f"## 사용자 원래 요청:\n{prompt}\n\n"
        "JSON 외 어떤 텍스트도 출력하지 말고, 마크다운 코드 펜스(```)도 사용하지 마십시오."
    )


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


def _parse_and_validate_plan(raw_plan: str, allowed_mcp_catalog_ids: set | None = None) -> WorkflowPlanSchema:
    cleaned = raw_plan.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.rstrip().endswith("```"):
        cleaned = "\n".join(cleaned.rstrip().split("\n")[:-1])
    cleaned = cleaned.strip()

    data = json.loads(cleaned)
    plan = WorkflowPlanSchema(**data)
    PlanValidator.validate(plan, allowed_mcp_catalog_ids)
    return plan


async def run_generate_agent(
    prompt: str,
    model: str,
    provider: str,
    api_key: str,
    env_key: str | None,
    validate_fn: ValidateFn | None = None,
    max_builder_retries: int = _MAX_BUILDER_RETRIES,
    available_mcp_servers: list | None = None,
    allowed_mcp_catalog_ids: set | None = None,
) -> str:
    """Orchestrator 없이 파이썬 코드로 Planner(Plan생성/검증) ➡️ Builder를 직접 순차 실행한다.

    validate_fn이 주어지면 Builder 단계에서 검증을 수행하고, 실패 시 동일 Plan을 유지한 채
    Builder에게만 결함 JSON과 검증 오류를 재투입하는 Reflexion 루프를 수행한다.
    (Planner는 재실행하지 않는다 — config·도구이름 등 Builder 책임 오류를 재기획으로 고칠 수 없기 때문)
    """
    prev_value = os.environ.get(env_key) if env_key else None
    try:
        if env_key:
            os.environ[env_key] = api_key

        planner_agent = build_planner_agent(model, prompt, provider, available_mcp_servers)

        # 1. 계획(Plan) 생성 1차 시도
        plan_raw = await _run_single_agent(planner_agent, prompt, _GENERATE_USER_ID)

        try:
            plan = _parse_and_validate_plan(plan_raw, allowed_mcp_catalog_ids)
        except Exception as first_err:
            logger.warning("1차 계획(Plan) 검증 실패: %s. 1회 자가 교정을 시도합니다.", str(first_err))
            feedback = (
                f"당신이 이전에 작성한 계획(Plan)에 설계상 결함이 발견되어 검증에 실패했습니다.\n"
                f"오류 피드백을 수용하여 사용자 요청에 부합하는 올바른 JSON Plan을 재생성하십시오.\n\n"
                f"## 오류 피드백:\n{str(first_err)}\n\n"
                f"## 사용자 원래 요청:\n{prompt}"
            )
            plan_raw = await _run_single_agent(planner_agent, feedback, _GENERATE_USER_ID)
            plan = _parse_and_validate_plan(plan_raw, allowed_mcp_catalog_ids)

        logger.info("성공적으로 워크플로우 계획(Plan)이 검증 통과했습니다. Justification: %s", plan.justification)

        # 2. 최종 워크플로우 빌드 (+ Builder 대상 Reflexion 루프)
        builder_agent = build_builder_agent(model, prompt, provider, available_mcp_servers)
        builder_prompt = (
            f"사용자 원래 요청: {prompt}\n\n"
            f"현재 요청 컨텍스트:\n- provider: {provider.upper()}\n"
            f"  (모든 AI 노드의 llmProvider는 반드시 \"{provider.upper()}\"로 설정해야 합니다)\n\n"
            f"수립된 워크플로우 계획 (반드시 준수할 것):\n{plan.model_dump_json(indent=2)}\n\n"
            f"위 계획의 구조(노드 수, 연결 흐름)를 철저히 준수하여 최종 워크플로우 JSON을 생성하십시오."
        )

        workflow_raw = await _run_single_agent(builder_agent, builder_prompt, _GENERATE_USER_ID)

        # validate_fn 미주입 시 기존 동작(검증 없이 raw 반환)을 유지한다.
        if validate_fn is None:
            return workflow_raw

        last_err: Exception | None = None
        for attempt in range(max_builder_retries + 1):
            try:
                validate_fn(workflow_raw)
                if attempt > 0:
                    logger.info("Builder Reflexion 루프 %d회 만에 검증 통과", attempt)
                return workflow_raw
            except Exception as e:
                last_err = e
                if attempt >= max_builder_retries:
                    break
                logger.warning(
                    "Builder 산출물 검증 실패(시도 %d/%d): %s. Builder에 결함 JSON과 오류를 재투입합니다.",
                    attempt + 1, max_builder_retries, str(e),
                )
                reflexion_prompt = _build_reflexion_prompt(
                    prompt, provider, plan, workflow_raw, str(e)
                )
                workflow_raw = await _run_single_agent(
                    builder_agent, reflexion_prompt, _GENERATE_USER_ID
                )

        # 모든 재시도 소진 — 마지막 오류를 전파한다.
        raise last_err

    finally:
        if env_key:
            if prev_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = prev_value
