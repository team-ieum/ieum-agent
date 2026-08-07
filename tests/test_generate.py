import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.workflow_generator import generate_workflow


VALID_PLAN_JSON = """{
  "nodes": [
    {"id": "node-1", "templateId": "trigger.schedule", "role": "매일 오전 9시", "description": "스케줄 트리거"},
    {"id": "node-2", "templateId": "ai.web_search", "role": "경제 뉴스 검색", "description": "경제 뉴스를 검색해줘"}
  ],
  "edges": [
    {"source": "node-1", "target": "node-2"}
  ],
  "justification": "스케줄 트리거 이후 검색 AI로 매핑함"
}"""

# Builder는 draft({id, templateId, slots})를 출력하고, 시스템이 템플릿으로 하이드레이션한다.
VALID_WORKFLOW_JSON = json.dumps({
    "nodes": [
        {"id": "node-1", "templateId": "trigger.schedule",
         "slots": {"label": "매일 오전 9시", "description": "이 노드가 하는 일을 쉽게 설명해요.", "cron": "0 9 * * *"}},
        {"id": "node-2", "templateId": "ai.web_search",
         "slots": {"label": "경제 뉴스 검색", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "경제 뉴스를 검색해 핵심만 반환해줘"}}
    ],
    "edges": [
        {"source": "node-1", "target": "node-2"}
    ]
})


def _make_runner_mock(outputs: list):
    """지정한 텍스트 리스트를 순차적으로 최종 응답으로 반환하는 Runner mock."""
    output_iter = iter(outputs)

    async def mock_run_async(**kwargs):
        try:
            val = next(output_iter)
        except StopIteration:
            val = ""
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": val})()]
        yield mock_event

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async
    return mock_runner


@pytest.fixture
def mock_adk():
    """ADK Runner, InMemorySessionService, env_lock을 mock으로 교체하는 공통 픽스처."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"

    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    # 1차 Plan ➡️ 2차 Workflow JSON
    outputs = [VALID_PLAN_JSON, VALID_WORKFLOW_JSON]

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock), \
         patch("core.workflow_generator.generate_workflow_logs.insert_one", AsyncMock()):
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
async def test_generate_workflow_모든_노드에_description(mock_adk):
    """생성 응답의 모든 노드는 비어 있지 않은 description을 갖는다(FE 노드 카드용)."""
    result = await generate_workflow("매일 9시에 경제뉴스 정리해줘", "CLAUDE", "test-key")

    assert all(n.description.strip() for n in result.nodes)


@pytest.mark.asyncio
async def test_generate_workflow_description_누락시_실패():
    """Builder가 description을 빠뜨리면 하이드레이션이 거부해 생성이 실패한다.
    (실서비스에서는 이 오류가 Builder Reflexion 루프로 되먹여져 재작성된다.)"""
    broken = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.schedule",
             "slots": {"label": "매일 오전 9시", "cron": "0 9 * * *"}},
            {"id": "node-2", "templateId": "ai.web_search",
             "slots": {"label": "검색", "prompt": "경제 뉴스를 검색해줘"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2"}],
    })
    # Builder 재시도(2회)까지 모두 동일한 결함 출력을 반환시켜 최종 실패를 확인한다.
    outputs = [VALID_PLAN_JSON, broken, broken, broken]

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock), \
         patch("core.workflow_generator.generate_workflow_logs.insert_one", AsyncMock()):
        with pytest.raises(ValueError, match="description"):
            await generate_workflow("테스트", "CLAUDE", "test-key")


@pytest.mark.asyncio
async def test_generate_workflow_코드펜스_제거():
    """LLM이 마크다운 코드 펜스로 감싸 반환해도 정상 파싱된다."""
    fenced_output = f"```json\n{VALID_WORKFLOW_JSON}\n```"
    outputs = [VALID_PLAN_JSON, fenced_output]

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        result = await generate_workflow("테스트", "CLAUDE", "test-key")
        assert len(result.nodes) == 2


@pytest.mark.asyncio
async def test_generate_workflow_서론_후행_텍스트_제거():
    """LLM이 JSON 앞뒤로 설명문을 덧붙여도 JSON 객체만 추출해 파싱된다 (A1)."""
    noisy_output = f"요청하신 워크플로우입니다:\n{VALID_WORKFLOW_JSON}\n이대로 사용하시면 됩니다."
    outputs = [VALID_PLAN_JSON, noisy_output]

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        result = await generate_workflow("테스트", "CLAUDE", "test-key")
        assert len(result.nodes) == 2


@pytest.mark.asyncio
async def test_generate_workflow_프롬프트_내_중괄호_보존():
    """노드 프롬프트의 변수참조({{...}})로 중괄호가 섞여도 JSON 추출이 깨지지 않는다 (A1)."""
    workflow_with_braces = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "수동", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
            {"id": "node-2", "templateId": "ai.web_search",
             "slots": {"label": "요약", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "이전 결과 {{nodes.node-1.output.triggeredAt}}를 요약"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2"}],
    })
    # 이 워크플로우와 정합하는 Plan(node-1=trigger.manual). _apply_plan_templates가 plan templateId를 강제하므로.
    manual_plan = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "role": "수동", "description": "수동 시작"},
            {"id": "node-2", "templateId": "ai.web_search", "role": "요약", "description": "요약"},
        ],
        "edges": [{"source": "node-1", "target": "node-2"}],
        "justification": "수동 트리거 후 요약",
    })
    outputs = [manual_plan, f"```json\n{workflow_with_braces}\n```"]

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        result = await generate_workflow("테스트", "CLAUDE", "test-key")
        assert len(result.nodes) == 2
        assert "{{nodes.node-1.output.triggeredAt}}" in result.nodes[1].config["prompt"]


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

    # 1차 Plan ➡️ Builder 빈 응답(+ Reflexion 재시도도 빈 응답)
    outputs = [VALID_PLAN_JSON, "", "", ""]

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
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

    # 1차 Plan ➡️ 2차 Workflow(잘못된 JSON) ➡️ 자가교정(잘못된 JSON)
    outputs = [VALID_PLAN_JSON, "이건 JSON이 아닙니다", VALID_PLAN_JSON, "이건 JSON이 아닙니다"]

    with patch("agents.generate.factory.Runner", return_value=_make_runner_mock(outputs)), \
         patch("agents.generate.factory.InMemorySessionService", return_value=mock_session_service), \
         patch("core.workflow_generator.get_env_lock", return_value=mock_lock):

        with pytest.raises(ValueError, match="JSON 파싱 실패"):
            await generate_workflow("테스트", "CLAUDE", "test-key")
