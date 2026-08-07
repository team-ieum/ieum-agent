import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.workflow_modifier import modify_workflow


VALID_MODIFY_JSON = json.dumps({
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
            "label": "뉴스 요약",
            "config": {
                "llmProvider": "CLAUDE",
                "credentialId": "",
                "prompt": "요약해줘",
                "agentType": "simple",
                "tools": []
            }
        },
        {
            "id": "node-3",
            "type": "AI",
            "label": "Slack 알림",
            "config": {
                "llmProvider": "CLAUDE",
                "credentialId": "",
                "prompt": "Slack으로 보내줘",
                "agentType": "react",
                "tools": [{"name": "slack"}]
            }
        }
    ],
    "edges": [
        {"source": "node-1", "target": "node-2", "conditionType": None},
        {"source": "node-2", "target": "node-3", "conditionType": None}
    ],
    "changeDescription": "Slack 알림 노드(node-3)를 node-2 다음에 추가했습니다."
})

CURRENT_NODES = [
    {"id": "node-1", "type": "TRIGGER", "label": "매일 오전 9시", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},
    {"id": "node-2", "type": "AI", "label": "뉴스 요약", "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "요약해줘", "agentType": "simple", "tools": []}},
]

CURRENT_EDGES = [
    {"source": "node-1", "target": "node-2", "conditionType": None},
]


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


def _make_patches(text_output: str):
    """core.workflow_modifier용 mock 패치 컨텍스트를 반환한다."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    return (
        patch("core.workflow_modifier.Runner", return_value=_make_runner_mock(text_output)),
        patch("core.workflow_modifier.InMemorySessionService", return_value=mock_session_service),
        patch("core.workflow_modifier.get_env_lock", return_value=mock_lock),
        patch("core.workflow_modifier._save_modify_workflow_log", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_modify_workflow_정상_json_파싱():
    """유효한 수정 결과 JSON을 반환하면 ModifyWorkflowResponse로 파싱된다."""
    p1, p2, p3, p4 = _make_patches(VALID_MODIFY_JSON)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "Slack 알림 노드를 추가해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert len(result.nodes) == 3
    assert result.nodes[0].type == "TRIGGER"
    assert result.nodes[2].label == "Slack 알림"
    assert len(result.edges) == 2
    assert result.rawPrompt == "Slack 알림 노드를 추가해줘"
    assert result.changeDescription == "Slack 알림 노드(node-3)를 node-2 다음에 추가했습니다."


@pytest.mark.asyncio
async def test_modify_workflow_코드펜스_제거():
    """LLM이 마크다운 코드 펜스로 감싸 반환해도 정상 파싱된다."""
    fenced_output = f"```json\n{VALID_MODIFY_JSON}\n```"
    p1, p2, p3, p4 = _make_patches(fenced_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "테스트", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )
    assert len(result.nodes) == 3


@pytest.mark.asyncio
async def test_modify_workflow_빈_응답_에러():
    """LLM이 빈 응답을 반환하면 ValueError가 발생한다."""
    p1, p2, p3, p4 = _make_patches("")
    with p1, p2, p3, p4:
        with pytest.raises(ValueError, match="파싱에 실패했습니다"):
            await modify_workflow(
                "테스트", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
            )


@pytest.mark.asyncio
async def test_modify_workflow_잘못된_json_에러():
    """LLM이 잘못된 JSON을 반환하면 ValueError가 발생한다."""
    p1, p2, p3, p4 = _make_patches("이건 JSON이 아닙니다")
    with p1, p2, p3, p4:
        with pytest.raises(ValueError, match="파싱에 실패했습니다"):
            await modify_workflow(
                "테스트", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
            )


@pytest.mark.asyncio
async def test_modify_workflow_description_보존():
    """LLM이 응답에서 description을 빠뜨려도 기존 노드의 설명이 보존된다.

    이 경로는 재검증·재시도 루프가 없어 한 번 누락되면 그대로 저장된다(사용자용 설명 소실).
    새로 추가된 노드의 description은 LLM이 준 값을 그대로 쓴다."""
    described_nodes = [
        {**n, "description": f"{n['label']} 노드가 하는 일을 쉽게 설명해요."}
        for n in CURRENT_NODES
    ]
    p1, p2, p3, p4 = _make_patches(VALID_MODIFY_JSON)  # description 없는 응답
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "Slack 알림 노드를 추가해줘", described_nodes, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert result.nodes[0].description == "매일 오전 9시 노드가 하는 일을 쉽게 설명해요."
    assert result.nodes[1].description == "뉴스 요약 노드가 하는 일을 쉽게 설명해요."
    assert result.nodes[2].description == ""  # 신규 노드는 되살릴 원본이 없다


@pytest.mark.asyncio
async def test_modify_workflow_llm_description_우선():
    """LLM이 description을 새로 써 보내면 그 값이 우선한다(설명 수정 요청 반영)."""
    payload = json.loads(VALID_MODIFY_JSON)
    payload["nodes"][0]["description"] = "정해둔 시각이 되면 자동으로 시작돼요."
    described_nodes = [{**n, "description": "예전 설명"} for n in CURRENT_NODES]

    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "설명을 다시 써줘", described_nodes, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert result.nodes[0].description == "정해둔 시각이 되면 자동으로 시작돼요."
