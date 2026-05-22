import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_generate import VALID_WORKFLOW_JSON


def _make_runner_mock(text_output: str):
    """지정한 텍스트를 최종 응답으로 반환하는 Runner mock."""
    mock_event = MagicMock()
    mock_event.is_final_response.return_value = True
    mock_event.content.parts = [type("Part", (), {"text": text_output})()]

    async def mock_run_async(**kwargs):
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
    agent = build_planner_agent("gemini-2.5-flash")
    assert agent.name == "planner_agent"


def test_build_planner_agent_instruction_mentions_planning():
    from agents.generate.sub.planner_agent import build_planner_agent
    agent = build_planner_agent("gemini-2.5-flash")
    instr_lower = agent.instruction.lower()
    # 한국어 instruction: "워크플로우" 또는 "계획" 포함 확인
    assert "워크플로우" in agent.instruction or "계획" in agent.instruction or "plan" in instr_lower or "workflow" in instr_lower


def test_build_planner_agent_has_no_tools():
    from agents.generate.sub.planner_agent import build_planner_agent
    agent = build_planner_agent("gemini-2.5-flash")
    assert not agent.tools


# ---------- build_builder_agent ----------

def test_build_builder_agent_has_correct_name():
    from agents.generate.sub.builder_agent import build_builder_agent
    agent = build_builder_agent("gemini-2.5-flash")
    assert agent.name == "builder_agent"


def test_build_builder_agent_instruction_contains_system_prompt_content():
    from agents.generate.sub.builder_agent import build_builder_agent
    agent = build_builder_agent("gemini-2.5-flash")
    assert "TRIGGER" in agent.instruction
    assert "JSON" in agent.instruction
    assert "workflow" in agent.instruction.lower()


# ---------- run_generate_agent ----------

@pytest.mark.asyncio
async def test_run_generate_agent_returns_raw_json_string():
    """Runner mock이 VALID_WORKFLOW_JSON을 반환하면 그대로 반환한다."""
    from agents.generate.factory import run_generate_agent

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(VALID_WORKFLOW_JSON)), \
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
async def test_run_generate_agent_injects_provider_in_instruction():
    """LlmAgent 생성 시 instruction에 provider가 포함된다."""
    from agents.generate.factory import run_generate_agent

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock("output")), \
         patch("agents.generate.factory.InMemorySessionService", return_value=_make_session_service()), \
         patch("agents.generate.factory.build_planner_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.build_builder_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.LlmAgent") as mock_llm_cls, \
         patch("agents.generate.factory.AgentTool", side_effect=lambda agent: MagicMock()):
        await run_generate_agent(
            prompt="test",
            model="gemini-2.5-flash",
            provider="CLAUDE",
            api_key="test-key",
            env_key=None,
        )

    _, kwargs = mock_llm_cls.call_args
    assert "CLAUDE" in kwargs.get("instruction", "")


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
         patch("agents.generate.factory.build_builder_agent", return_value=MagicMock()), \
         patch("agents.generate.factory.AgentTool", side_effect=lambda agent: MagicMock()):
        with pytest.raises(Exception):
            await run_generate_agent(
                prompt="test",
                model="gemini-2.5-flash",
                provider="CLAUDE",
                api_key="test-key",
                env_key=env_key,
            )

    assert env_key not in os.environ
