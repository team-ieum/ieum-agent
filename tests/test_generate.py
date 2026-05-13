import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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


def _make_runner_mock(text_output: str):
    """지정한 텍스트를 최종 응답으로 반환하는 Runner mock을 생성한다."""
    mock_event = MagicMock()
    mock_event.is_final_response.return_value = True
    mock_event.content.parts = [type("Part", (), {"text": text_output})()]

    async def mock_run_async(**kwargs):
        yield mock_event

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async
    return mock_runner


@pytest.fixture
def mock_adk(request):
    """
    ADK Runner, InMemorySessionService, env_lock을 mock으로 교체하는 공통 픽스처.
    request.param으로 LLM 출력 텍스트를 주입한다.
    """
    text_output = getattr(request, "param", VALID_WORKFLOW_JSON)

    mock_session = AsyncMock()
    mock_session.id = "test-session"

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_generator.Runner", return_value=_make_runner_mock(text_output)), \
         patch("core.workflow_generator.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):
        yield


@pytest.mark.asyncio
async def test_generate_workflow_정상_json_파싱(mock_adk):
    """LLM이 정상 JSON을 반환하면 GenerateWorkflowResponse로 파싱된다."""
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

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_generator.Runner", return_value=_make_runner_mock(fenced_output)), \
         patch("core.workflow_generator.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        result = await generate_workflow("테스트", "CLAUDE", "test-key")
        assert len(result.nodes) == 2


@pytest.mark.asyncio
async def test_generate_workflow_빈_응답_에러():
    """LLM이 빈 응답을 반환하면 ValueError가 발생한다."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_generator.Runner", return_value=_make_runner_mock("")), \
         patch("core.workflow_generator.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        with pytest.raises(ValueError, match="빈 응답"):
            await generate_workflow("테스트", "CLAUDE", "test-key")


@pytest.mark.asyncio
async def test_generate_workflow_잘못된_json_에러():
    """LLM이 잘못된 JSON을 반환하면 ValueError가 발생한다."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_generator.Runner", return_value=_make_runner_mock("이건 JSON이 아닙니다")), \
         patch("core.workflow_generator.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        with pytest.raises(ValueError, match="JSON 파싱 실패"):
            await generate_workflow("테스트", "CLAUDE", "test-key")
