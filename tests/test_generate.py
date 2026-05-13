import json
import pytest
from unittest.mock import AsyncMock, patch

from api.schemas.generate_workflow import GenerateWorkflowRequest
from core.workflow_generator import generate_workflow


VALID_WORKFLOW_JSON = json.dumps({
    "nodes": [
        {
            "id": "node-1",
            "type": "TRIGGER",
            "label": "매일 오전 9시",
            "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}
        },
        {
            "id": "node-2",
            "type": "AI",
            "label": "경제 뉴스 요약",
            "config": {
                "llmProvider": "CLAUDE",
                "credentialId": "",
                "prompt": "경제 뉴스를 요약해줘",
                "agentType": "react",
                "tools": [{"name": "builtin:http_fetch"}, {"name": "builtin:notion_create_page"}]
            }
        }
    ],
    "edges": [
        {"source": "node-1", "target": "node-2", "conditionType": None}
    ]
})


@pytest.mark.asyncio
async def test_generate_workflow_정상_json_파싱():
    """LLM이 정상 JSON을 반환하면 GenerateWorkflowResponse로 파싱된다."""
    with patch("core.workflow_generator.Runner") as mock_runner_cls, \
         patch("core.workflow_generator.InMemorySessionService") as mock_session_cls, \
         patch("core.workflow_generator.get_env_lock") as mock_lock:

        # Lock mock
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        # Session mock
        mock_session = AsyncMock()
        mock_session.id = "test-session"
        mock_session_cls.return_value.create_session = AsyncMock(return_value=mock_session)

        # Runner mock — 정상 JSON 반환
        mock_event = AsyncMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": VALID_WORKFLOW_JSON})()]

        async def mock_run_async(**kwargs):
            yield mock_event

        mock_runner_cls.return_value.run_async = mock_run_async

        result = await generate_workflow("매일 9시에 경제뉴스 정리해줘", "CLAUDE", "test-key")

        assert len(result.nodes) == 2
        assert result.nodes[0].type == "TRIGGER"
        assert result.nodes[1].type == "AI"
        assert len(result.edges) == 1
        assert result.rawPrompt == "매일 9시에 경제뉴스 정리해줘"


@pytest.mark.asyncio
async def test_generate_workflow_코드펜스_제거():
    """LLM이 마크다운 코드 펜스로 감싸 반환해도 정상 파싱된다."""
    fenced_output = f"```json\n{VALID_WORKFLOW_JSON}\n```"

    with patch("core.workflow_generator.Runner") as mock_runner_cls, \
         patch("core.workflow_generator.InMemorySessionService") as mock_session_cls, \
         patch("core.workflow_generator.get_env_lock") as mock_lock:

        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.id = "test-session"
        mock_session_cls.return_value.create_session = AsyncMock(return_value=mock_session)

        mock_event = AsyncMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": fenced_output})()]

        async def mock_run_async(**kwargs):
            yield mock_event

        mock_runner_cls.return_value.run_async = mock_run_async

        result = await generate_workflow("테스트", "CLAUDE", "test-key")
        assert len(result.nodes) == 2


@pytest.mark.asyncio
async def test_generate_workflow_빈_응답_에러():
    """LLM이 빈 응답을 반환하면 ValueError가 발생한다."""
    with patch("core.workflow_generator.Runner") as mock_runner_cls, \
         patch("core.workflow_generator.InMemorySessionService") as mock_session_cls, \
         patch("core.workflow_generator.get_env_lock") as mock_lock:

        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.id = "test-session"
        mock_session_cls.return_value.create_session = AsyncMock(return_value=mock_session)

        mock_event = AsyncMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": ""})()]

        async def mock_run_async(**kwargs):
            yield mock_event

        mock_runner_cls.return_value.run_async = mock_run_async

        with pytest.raises(ValueError, match="빈 응답"):
            await generate_workflow("테스트", "CLAUDE", "test-key")


@pytest.mark.asyncio
async def test_generate_workflow_잘못된_json_에러():
    """LLM이 잘못된 JSON을 반환하면 ValueError가 발생한다."""
    with patch("core.workflow_generator.Runner") as mock_runner_cls, \
         patch("core.workflow_generator.InMemorySessionService") as mock_session_cls, \
         patch("core.workflow_generator.get_env_lock") as mock_lock:

        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.id = "test-session"
        mock_session_cls.return_value.create_session = AsyncMock(return_value=mock_session)

        mock_event = AsyncMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": "이건 JSON이 아닙니다"})()]

        async def mock_run_async(**kwargs):
            yield mock_event

        mock_runner_cls.return_value.run_async = mock_run_async

        with pytest.raises(ValueError, match="JSON 파싱 실패"):
            await generate_workflow("테스트", "CLAUDE", "test-key")
