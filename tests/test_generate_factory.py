import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.schemas.generate_workflow import WorkflowPlanSchema
from core.validators.plan_validator import PlanValidationError

VALID_PLAN_JSON = """{
  "nodes": [
    {"id": "node-1", "type": "TRIGGER", "role": "매일 아침 9시 트리거", "description": "스케줄러"}
  ],
  "edges": [],
  "justification": "스케줄 트리거로 시작해야 하므로 node-1에 TRIGGER를 구성함."
}"""

VALID_WORKFLOW_JSON = """{
  "nodes": [
    {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}}
  ],
  "edges": [],
  "rawPrompt": "테스트"
}"""


def _make_runner_mock(outputs: list):
    """순차적으로 지정한 텍스트를 최종 응답으로 반환하는 Runner mock."""
    output_iter = iter(outputs)

    async def mock_run_async(*args, **kwargs):
        text_output = next(output_iter)
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": text_output})()]
        yield mock_event

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async
    return mock_runner


def _make_session_service():
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_ss = MagicMock()
    mock_ss.create_session = AsyncMock(return_value=mock_session)
    return mock_ss


def _make_lock():
    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    return lock


# ---------- build_planner_agent ----------

def test_build_planner_agent_has_correct_name():
    from agents.generate.sub.planner_agent import build_planner_agent
    agent = build_planner_agent("gemini-2.5-flash", "test-prompt", "CLAUDE")
    assert agent.name == "planner_agent"


def test_build_planner_agent_instruction_mentions_planning():
    from agents.generate.sub.planner_agent import build_planner_agent
    agent = build_planner_agent("gemini-2.5-flash", "test-prompt", "CLAUDE")
    instr_lower = agent.instruction.lower()
    assert "워크플로우" in agent.instruction or "계획" in agent.instruction or "plan" in instr_lower or "workflow" in instr_lower


# ---------- build_builder_agent ----------

def test_build_builder_agent_has_correct_name():
    from agents.generate.sub.builder_agent import build_builder_agent
    agent = build_builder_agent("gemini-2.5-flash", "test-prompt", "CLAUDE")
    assert agent.name == "builder_agent"


def test_build_builder_agent_instruction_contains_system_prompt_content():
    from agents.generate.sub.builder_agent import build_builder_agent
    agent = build_builder_agent("gemini-2.5-flash", "test-prompt", "CLAUDE")
    assert "TRIGGER" in agent.instruction
    assert "JSON" in agent.instruction
    assert "workflow" in agent.instruction.lower()


# ---------- run_generate_agent ----------

@pytest.mark.asyncio
async def test_run_generate_agent_returns_raw_json_string():
    """Runner mock이 순서대로 VALID_PLAN_JSON, VALID_WORKFLOW_JSON을 반환하면 최종 JSON을 반환한다."""
    from agents.generate.factory import run_generate_agent

    mock_runner = _make_runner_mock([VALID_PLAN_JSON, VALID_WORKFLOW_JSON])

    with patch("agents.generate.factory.Runner", return_value=mock_runner), \
         patch("agents.generate.factory.InMemorySessionService", return_value=_make_session_service()), \
         patch("agents.generate.factory.build_planner_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.build_builder_agent", return_value=MagicMock()):
        result = await run_generate_agent(
            prompt="매일 9시에 경제뉴스 정리해줘",
            model="gemini-2.5-flash",
            provider="CLAUDE",
            api_key="test-key",
            env_key=None,
        )

    assert result == VALID_WORKFLOW_JSON


@pytest.mark.asyncio
async def test_run_generate_agent_with_plan_retry():
    """1차 Plan 검증 실패 시 자가 교정 피드백 루프를 통해 2차에 성공한다."""
    from agents.generate.factory import run_generate_agent

    # 1차 Plan(실패: TRIGGER 없음) ➡️ 2차 Plan(성공) ➡️ Builder(성공)
    invalid_plan = '{"nodes": [{"id": "node-1", "type": "AI", "role": "역할", "description": "설명"}], "edges": [], "justification": "TRIGGER 누락됨"}'

    mock_runner = _make_runner_mock([invalid_plan, VALID_PLAN_JSON, VALID_WORKFLOW_JSON])

    with patch("agents.generate.factory.Runner", return_value=mock_runner), \
         patch("agents.generate.factory.InMemorySessionService", return_value=_make_session_service()), \
         patch("agents.generate.factory.build_planner_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.build_builder_agent", return_value=MagicMock()):

        result = await run_generate_agent(
            prompt="매일 9시에 경제뉴스 정리해줘",
            model="gemini-2.5-flash",
            provider="CLAUDE",
            api_key="test-key",
            env_key=None,
        )

    assert result == VALID_WORKFLOW_JSON


@pytest.mark.asyncio
async def test_run_generate_agent_restores_env_on_exception():
    """Runner 예외 발생 시 env_key가 os.environ에서 제거된다."""
    from agents.generate.factory import run_generate_agent

    env_key = "TEST_GENERATE_API_KEY_RESTORE"
    assert env_key not in os.environ

    mock_runner = MagicMock()
    mock_runner.run_async = MagicMock(side_effect=RuntimeError("runner error"))

    with patch("agents.generate.factory.Runner", return_value=mock_runner), \
         patch("agents.generate.factory.InMemorySessionService", return_value=_make_session_service()), \
         patch("agents.generate.factory.build_planner_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.build_builder_agent", return_value=MagicMock()):
        with pytest.raises(Exception):
            await run_generate_agent(
                prompt="test",
                model="gemini-2.5-flash",
                provider="CLAUDE",
                api_key="test-key",
                env_key=env_key,
            )

    assert env_key not in os.environ
