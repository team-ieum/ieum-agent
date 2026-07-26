import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from api.schemas.chat import ChatResponseType
from core.workflow_chat import chat_workflow

# Designer가 출력하는 draft 노드(templateId + slots). provider 슬롯은 시스템 자동 주입.
DRAFT_NODES = [
    {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
    {"id": "node-2", "templateId": "ai.web_search", "slots": {"label": "AI 처리", "prompt": "처리해줘"}},
]
# 저장된 full-node(수정 요청 시 current_nodes로 들어오는 영속 포맷). dehydrate 대상.
FULL_NODES = [
    {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {"triggerType": "MANUAL"}},
    {"id": "node-2", "type": "AI", "label": "AI 처리",
     "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "처리해줘",
                "agentType": "react", "tools": [{"name": "builtin:web_search"}]}},
]
VALID_EDGES = [{"source": "node-1", "target": "node-2", "conditionType": None}]

WORKFLOW_GENERATED_JSON = json.dumps({
    "message": "워크플로우를 생성했습니다.",
    "type": "WORKFLOW_GENERATED",
    "actions": [],
    "changeDescription": None,
    "nodes": DRAFT_NODES,
    "edges": VALID_EDGES,
    "workflowName": "IT 트렌드 자동 노션 요약",
})

WORKFLOW_MODIFIED_JSON = json.dumps({
    "message": "워크플로우를 수정했습니다.",
    "type": "WORKFLOW_MODIFIED",
    "actions": [],
    "changeDescription": "node-2 프롬프트를 수정했습니다.",
    "nodes": DRAFT_NODES,
    "edges": VALID_EDGES,
})

INTEGRATION_REQUIRED_OAUTH_JSON = json.dumps({
    "message": "Google 연동이 필요합니다.",
    "type": "INTEGRATION_REQUIRED",
    "actions": [{"type": "OAUTH", "provider": "GOOGLE"}],
    "changeDescription": None,
    "nodes": None,
    "edges": None,
})

INTEGRATION_REQUIRED_WEBHOOK_JSON = json.dumps({
    "message": "Slack 연동이 필요합니다.",
    "type": "INTEGRATION_REQUIRED",
    "actions": [],
    "changeDescription": None,
    "nodes": None,
    "edges": None,
})

CLARIFICATION_NEEDED_JSON = json.dumps({
    "message": "어느 Slack 채널로 보낼까요?",
    "type": "CLARIFICATION_NEEDED",
    "actions": [],
    "changeDescription": None,
    "nodes": None,
    "edges": None,
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


def _make_patches(text_output: str):
    """core.workflow_chat용 mock 패치 컨텍스트를 반환한다."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    return (
        patch("core.workflow_chat.Runner", return_value=_make_runner_mock(text_output)),
        patch("core.workflow_chat._get_session_service", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    )


def _make_seq_runner_mock(outputs: list):
    """호출마다 outputs를 순서대로 반환하는 Runner mock(마지막 값 반복). 자가 교정 순차 응답 테스트용."""
    state = {"i": 0}

    async def mock_run_async(**kwargs):
        i = state["i"]
        state["i"] += 1
        text = outputs[min(i, len(outputs) - 1)]
        ev = MagicMock()
        ev.is_final_response.return_value = True
        ev.content.parts = [type("Part", (), {"text": text})()]
        yield ev

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async
    return mock_runner


def _make_patches_seq(outputs: list):
    """Runner가 호출마다 다른 출력을 내도록 한 패치 컨텍스트(designer/reviewer/재생성 공유 카운터)."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    return (
        patch("core.workflow_chat.Runner", return_value=_make_seq_runner_mock(outputs)),
        patch("core.workflow_chat._get_session_service", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    )


async def _call(prompt="테스트", current_nodes=None, current_edges=None,
                available=None, unavailable=None, preserve_id=None,
                available_mcp_servers=None, available_webhooks=None,
                workflow_id=None):
    return await chat_workflow(
        prompt=prompt,
        provider="CLAUDE",
        api_key="test-key",
        user_id="test-user",
        workflow_id=workflow_id,
        available_integrations=available or [],
        unavailable_integrations=unavailable or [],
        current_nodes=current_nodes,
        current_edges=current_edges,
        preserve_id=preserve_id,
        available_mcp_servers=available_mcp_servers,
        available_webhooks=available_webhooks,
    )


@pytest.mark.asyncio
async def test_chat_workflow_신규생성_정상():
    """정상 WORKFLOW_GENERATED 응답이 draft 하이드레이션을 거쳐 파싱된다."""
    p1, p2, p3, p4 = _make_patches(WORKFLOW_GENERATED_JSON)
    with p1, p2, p3, p4:
        result = await _call("워크플로우 만들어줘")
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert len(result.nodes) == 2
    assert result.nodes[0].type == "TRIGGER"
    assert result.nodes[1].type == "AI"
    assert len(result.edges) == 1
    assert result.rawPrompt == "워크플로우 만들어줘"
    assert result.workflowName == "IT 트렌드 자동 노션 요약"


@pytest.mark.asyncio
async def test_chat_workflow_수정_정상():
    """currentNodes(full-node) 전달 시 WORKFLOW_MODIFIED, changeDescription이 존재한다."""
    p1, p2, p3, p4 = _make_patches(WORKFLOW_MODIFIED_JSON)
    with p1, p2, p3, p4:
        result = await _call(
            "프롬프트 수정해줘",
            current_nodes=FULL_NODES,
            current_edges=VALID_EDGES,
        )
    assert result.type == ChatResponseType.WORKFLOW_MODIFIED
    assert result.changeDescription is not None
    assert len(result.nodes) == 2
    assert result.workflowName is None


@pytest.mark.asyncio
async def test_chat_workflow_연동미완료_OAuth():
    """INTEGRATION_REQUIRED + OAuth 서비스: actions에 OAUTH 포함, oauthUrl 없음."""
    p1, p2, p3, p4 = _make_patches(INTEGRATION_REQUIRED_OAUTH_JSON)
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.INTEGRATION_REQUIRED
    assert len(result.actions) == 1
    assert result.actions[0].type.value == "OAUTH"
    assert result.actions[0].provider.value == "GOOGLE"
    assert not hasattr(result.actions[0], "oauthUrl")
    assert result.nodes is None


@pytest.mark.asyncio
async def test_chat_workflow_연동미완료_Webhook():
    """INTEGRATION_REQUIRED + Webhook 서비스: actions 비어있음."""
    p1, p2, p3, p4 = _make_patches(INTEGRATION_REQUIRED_WEBHOOK_JSON)
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.INTEGRATION_REQUIRED
    assert result.actions == []
    assert result.nodes is None


@pytest.mark.asyncio
async def test_chat_workflow_Webhook_여러개_되묻기():
    """Webhook credential 2개 전달 시 CLARIFICATION_NEEDED."""
    p1, p2, p3, p4 = _make_patches(CLARIFICATION_NEEDED_JSON)
    with p1, p2, p3, p4:
        result = await _call(
            available=[{
                "provider": "SLACK",
                "credentials": [
                    {"id": "c1", "displayName": "채널1"},
                    {"id": "c2", "displayName": "채널2"},
                ],
            }]
        )
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED
    assert result.nodes is None


@pytest.mark.asyncio
async def test_chat_workflow_불명확_요청():
    """불명확한 요청 시 CLARIFICATION_NEEDED, nodes == None."""
    p1, p2, p3, p4 = _make_patches(CLARIFICATION_NEEDED_JSON)
    with p1, p2, p3, p4:
        result = await _call("뭔가 해줘")
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED
    assert result.nodes is None


CLARIFICATION_WITH_OPTIONS_JSON = json.dumps({
    "message": "어느 GitHub 저장소로 할까요?",
    "type": "CLARIFICATION_NEEDED",
    "actions": [],
    "options": [
        {"value": "ieum/agent", "label": "agent", "description": None},
        {"value": "ieum/backend", "label": "backend"},
    ],
    "changeDescription": None,
    "nodes": None,
    "edges": None,
})


@pytest.mark.asyncio
async def test_chat_workflow_options_파싱():
    """CLARIFICATION_NEEDED 응답의 options(선택지)가 파싱된다."""
    p1, p2, p3, p4 = _make_patches(CLARIFICATION_WITH_OPTIONS_JSON)
    with p1, p2, p3, p4:
        result = await _call("GitHub PR을 노션에 저장해줘")
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED
    assert len(result.options) == 2
    assert result.options[0].value == "ieum/agent"
    assert result.options[0].label == "agent"
    assert result.options[1].value == "ieum/backend"


@pytest.mark.asyncio
async def test_chat_workflow_options_기본_빈배열():
    """options가 없는 응답은 빈 배열로 처리된다."""
    p1, p2, p3, p4 = _make_patches(CLARIFICATION_NEEDED_JSON)
    with p1, p2, p3, p4:
        result = await _call("뭔가 해줘")
    assert result.options == []


@pytest.mark.asyncio
async def test_chat_workflow_빈_응답_CLARIFICATION_폴백():
    """Designer가 (재시도 후에도) 빈 응답을 반환하면 502 대신 CLARIFICATION_NEEDED로 폴백한다."""
    p1, p2, p3, p4 = _make_patches("")
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED
    assert result.nodes is None
    assert "다시" in result.message


@pytest.mark.asyncio
async def test_chat_workflow_잘못된_json_폴백():
    """LLM이 잘못된 JSON을 반환하면 CLARIFICATION_NEEDED 타입으로 폴백된다."""
    p1, p2, p3, p4 = _make_patches("이건 JSON이 아닙니다")
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED
    assert result.message == "이건 JSON이 아닙니다"


@pytest.mark.asyncio
async def test_chat_workflow_코드펜스_제거():
    """마크다운 코드 펜스로 감싸도 정상 파싱된다."""
    fenced = f"```json\n{WORKFLOW_GENERATED_JSON}\n```"
    p1, p2, p3, p4 = _make_patches(fenced)
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert len(result.nodes) == 2


@pytest.mark.asyncio
async def test_chat_workflow_invalid_template_id():
    """존재하지 않는 templateId → 하이드레이션 실패 → 자가 교정 후에도 실패면 CLARIFICATION_NEEDED 폴백."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.bogus_does_not_exist", "slots": {"label": "x"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        result = await _call(preserve_id=True)
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED


@pytest.mark.asyncio
async def test_chat_workflow_duplicate_node_id():
    """중복된 node id → 자가 교정 재시도(동일 출력 반복) 후에도 검증 실패면 CLARIFICATION_NEEDED 폴백."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-1", "templateId": "ai.web_search", "slots": {"label": "중복", "prompt": "p"}},
        ],
        "edges": [],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        result = await _call(preserve_id=True)
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED


@pytest.mark.asyncio
async def test_chat_workflow_invalid_edge_reference():
    """존재하지 않는 node를 참조하는 edge → 자가 교정 실패 시 CLARIFICATION_NEEDED 폴백."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
        ],
        "edges": [
            {"source": "node-1", "target": "node-999", "conditionType": None},
        ],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        result = await _call(preserve_id=True)
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED


@pytest.mark.asyncio
async def test_chat_workflow_static_validation_self_correction_success():
    """정적 검증 실패 후 자가 교정 재생성으로 유효한 워크플로우를 복구한다.
    호출 순서: designer(잘못된 참조 문법) → reviewer(통과) → 재생성(정상) → WORKFLOW_GENERATED."""
    bad = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.web_search",
             "slots": {"label": "처리", "prompt": "오늘은 {{formatDate now 'YYYY-MM-DD'}}"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    reviewer_pass = json.dumps({"isValid": True, "feedback": None})
    # designer 초안(bad) → reviewer(pass) → 정적검증 실패 → 재생성(정상)
    p1, p2, p3, p4 = _make_patches_seq([bad, reviewer_pass, WORKFLOW_GENERATED_JSON])
    with p1, p2, p3, p4:
        result = await _call(preserve_id=True)
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes is not None


@pytest.mark.asyncio
async def test_chat_workflow_generated_nodes_없으면_clarification():
    """WORKFLOW_GENERATED인데 nodes가 None이면 자가 교정 후 CLARIFICATION_NEEDED로 폴백한다."""
    invalid = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": None,
        "edges": None,
    })
    p1, p2, p3, p4 = _make_patches(invalid)
    with p1, p2, p3, p4:
        result = await _call()
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED


@pytest.mark.asyncio
async def test_chat_workflow_with_mcp_servers_success():
    """mcp_servers 전달 시, MCPToolset이 생성되고 get_tools()가 호출되어 browse_tools에 주입된다."""
    p1, p2, p3, p4 = _make_patches(WORKFLOW_GENERATED_JSON)

    mock_mcp_instance = MagicMock()
    mock_mcp_instance.get_tools = MagicMock(return_value=[])

    with p1, p2, p3, p4, \
         patch("core.workflow_chat.MCPToolset", return_value=mock_mcp_instance), \
         patch("core.workflow_chat._safe_close_mcp") as mock_close:
        result = await chat_workflow(
            prompt="테스트",
            provider="CLAUDE",
            api_key="test-key",
            user_id="test-user",
            available_integrations=[],
            unavailable_integrations=[],
            mcp_servers=[{"server_url": "http://test-mcp-server/sse", "headers": {}}]
        )

    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    mock_mcp_instance.get_tools.assert_called_once()


@pytest.mark.asyncio
async def test_chat_workflow_id_translation():
    """preserve_id=False인 신규 생성 시 노드 ID가 node-1, node-2 등으로 바뀌고, 엣지와 변수 참조도 함께 바뀐다."""
    input_json = json.dumps({
        "message": "워크플로우를 생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "trigger_node", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "ai_node", "templateId": "ai.web_search",
             "slots": {"label": "AI 처리", "prompt": "이전 데이터: {{nodes.trigger_node.output.data}}"}},
        ],
        "edges": [
            {"source": "trigger_node", "target": "ai_node", "conditionType": "success"}
        ],
    })

    p1, p2, p3, p4 = _make_patches(input_json)
    with p1, p2, p3, p4:
        result = await _call("만들어줘", preserve_id=False)

    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    # 노드 ID 검증 (순서대로 정규화)
    assert result.nodes[0].id == "node-1"
    assert result.nodes[1].id == "node-2"

    # 엣지 ID 매핑 검증
    assert result.edges[0].source == "node-1"
    assert result.edges[0].target == "node-2"

    # 슬롯(prompt) 내 참조 변수 변경 검증
    assert result.nodes[1].config["prompt"] == "이전 데이터: {{nodes.node-1.output.data}}"


def test_chat_workflow_session_service_싱글톤():
    """_get_session_service가 MongoSessionService를 싱글톤으로 반환한다(매 호출 동일 인스턴스)."""
    import core.workflow_chat
    core.workflow_chat._SESSION_SERVICE = None
    with patch("core.workflow_chat.MongoSessionService", return_value=MagicMock()) as mock_cls:
        svc1 = core.workflow_chat._get_session_service()
        svc2 = core.workflow_chat._get_session_service()
    assert svc1 is svc2                 # 싱글톤
    assert mock_cls.call_count == 1     # 1회만 생성
    core.workflow_chat._SESSION_SERVICE = None


def _make_patches_with_service(text_output: str):
    """mock_session_service를 함께 반환하여 세션 호출을 검증할 수 있는 패치 셋."""
    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    patches = (
        patch("core.workflow_chat.Runner", return_value=_make_runner_mock(text_output)),
        patch("core.workflow_chat._get_session_service", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    )
    return patches, mock_session_service


@pytest.mark.asyncio
async def test_chat_workflow_workflow_id_있으면_세션_재사용():
    """workflow_id가 있으면 '{user_id}-{workflow_id}' 세션을 사용하고 삭제하지 않는다(멀티턴 유지)."""
    import core.workflow_chat
    core.workflow_chat._SESSION_SERVICE = None
    (p1, p2, p3, p4), svc = _make_patches_with_service(WORKFLOW_MODIFIED_JSON)
    with p1, p2, p3, p4:
        await _call(
            "프롬프트 수정해줘",
            current_nodes=FULL_NODES,
            current_edges=VALID_EDGES,
            workflow_id="wf-123",
        )
    # 워크플로우별 결정론적 세션 ID로 조회
    svc.get_session.assert_awaited_once()
    assert svc.get_session.await_args.kwargs["session_id"] == "test-user-wf-123"
    # 멀티턴 유지를 위해 삭제하지 않음
    svc.delete_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_workflow_workflow_id_없으면_격리_세션_생성_후_삭제():
    """workflow_id가 없으면(신규 생성) 매 요청 고유 세션을 만들고 종료 시 삭제한다."""
    import core.workflow_chat
    core.workflow_chat._SESSION_SERVICE = None
    (p1, p2, p3, p4), svc = _make_patches_with_service(WORKFLOW_GENERATED_JSON)
    with p1, p2, p3, p4:
        await _call("워크플로우 만들어줘")
    # 과거 대화 조회 없이 고유 세션을 새로 생성
    svc.get_session.assert_not_awaited()
    svc.create_session.assert_awaited_once()
    created_sid = svc.create_session.await_args.kwargs["session_id"]
    assert created_sid.startswith("test-user-")
    assert created_sid != "test-user"  # uuid 접미사가 붙어 user_id와 다름
    # 종료 시 격리 세션 폐기
    svc.delete_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_workflow_self_correction_loop():
    """리뷰어가 결함을 발견하면 피드백을 수용해 자가 교정(Retry)을 거쳐 최종 결과를 반환한다."""
    # 1. 초안: http 템플릿(디스코드 직접 호출 — 리뷰어가 반려)
    faulty_draft_json = json.dumps({
        "message": "초안 생성",
        "type": "WORKFLOW_GENERATED",
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "http",
             "slots": {"label": "디스코드 전송", "method": "POST", "url": "https://discord.com/api/webhooks/x"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    reviewer_feedback_json = json.dumps({
        "isValid": False,
        "feedback": "외부 연동은 http 템플릿 대신 ai.discord_send 템플릿을 사용하십시오.",
    })
    corrected_workflow_json = json.dumps({
        "message": "교정 완료",
        "type": "WORKFLOW_GENERATED",
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.discord_send", "slots": {"label": "디스코드 전송", "prompt": "발송"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })

    outputs = [faulty_draft_json, reviewer_feedback_json, corrected_workflow_json]
    call_index = 0

    def mock_run_async_seq(**kwargs):
        nonlocal call_index
        text_output = outputs[call_index]
        call_index += 1
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": text_output})()]

        async def generator():
            yield mock_event
        return generator()

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async_seq

    mock_session = AsyncMock()
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_chat.Runner", return_value=mock_runner), \
         patch("core.workflow_chat._get_session_service", return_value=mock_session_service), \
         patch("core.workflow_chat.get_env_lock", return_value=mock_lock), \
         patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock):
        result = await chat_workflow(
            prompt="디스코드 전송 워크플로우 만들어줘",
            provider="CLAUDE",
            api_key="test-key",
            user_id="test-user",
            available_integrations=[],
            unavailable_integrations=[],
        )

    assert call_index == 3
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes[1].type == "AI"
    # ai.discord_send 하이드레이션 결과 tools에 discord 도구가 들어간다
    assert {"name": "discord"} in result.nodes[1].config["tools"]


@pytest.mark.asyncio
async def test_chat_workflow_schedule_trigger_success():
    """SCHEDULE 트리거 draft(cron 슬롯)가 하이드레이션되어 정상 파싱된다."""
    schedule_json = json.dumps({
        "message": "스케줄 워크플로우를 생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "templateId": "trigger.schedule", "slots": {"label": "트리거", "cron": "0 17 * * 5"}},
            {"id": "node-2", "templateId": "ai.web_search", "slots": {"label": "AI 처리", "prompt": "처리해줘"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
        "workflowName": "IT 트렌드 자동 노션 요약",
    })
    p1, p2, p3, p4 = _make_patches(schedule_json)
    with p1, p2, p3, p4:
        result = await _call("매주 금요일 17시 실행 스케줄 워크플로우 만들어줘")

    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes[0].config["triggerType"] == "SCHEDULE"
    assert result.nodes[0].config["cron"] == "0 17 * * 5"


@pytest.mark.asyncio
async def test_chat_workflow_schedule_trigger_correction():
    """SCHEDULE 트리거에 cron 슬롯이 없어 하이드레이션 실패 → 자가 교정으로 cron을 채워 성공한다."""
    # 1. 초안: trigger.schedule인데 cron 슬롯 누락 → 필수 슬롯 누락으로 하이드레이션 실패
    faulty_draft_json = json.dumps({
        "message": "초안 생성",
        "type": "WORKFLOW_GENERATED",
        "nodes": [
            {"id": "node-1", "templateId": "trigger.schedule", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.web_search", "slots": {"label": "AI 처리", "prompt": "처리해줘"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    reviewer_feedback_json = json.dumps({
        "isValid": False,
        "feedback": "trigger.schedule 노드에 cron 슬롯이 누락되었습니다.",
    })
    corrected_workflow_json = json.dumps({
        "message": "교정 완료",
        "type": "WORKFLOW_GENERATED",
        "nodes": [
            {"id": "node-1", "templateId": "trigger.schedule", "slots": {"label": "트리거", "cron": "0 17 * * 5"}},
            {"id": "node-2", "templateId": "ai.web_search", "slots": {"label": "AI 처리", "prompt": "처리해줘"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })

    outputs = [faulty_draft_json, reviewer_feedback_json, corrected_workflow_json]
    call_index = 0

    def mock_run_async_seq(**kwargs):
        nonlocal call_index
        text_output = outputs[call_index]
        call_index += 1
        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.content.parts = [type("Part", (), {"text": text_output})()]

        async def generator():
            yield mock_event
        return generator()

    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async_seq

    mock_session = MagicMock()
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)
    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    with patch("core.workflow_chat.Runner", return_value=mock_runner), \
         patch("core.workflow_chat._get_session_service", return_value=mock_session_service), \
         patch("core.workflow_chat.get_env_lock", return_value=mock_lock), \
         patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock):
        result = await chat_workflow(
            prompt="매주 금요일 17시 스케줄 워크플로우 만들어줘",
            provider="CLAUDE",
            api_key="test-key",
            user_id="test-user",
            available_integrations=[],
            unavailable_integrations=[],
        )

    assert call_index == 3
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes[0].config["triggerType"] == "SCHEDULE"
    assert result.nodes[0].config["cron"] == "0 17 * * 5"


# ── 역하이드레이션(MODIFY) + 웹훅 자격 검증 ─────────────────────────────────

def test_dehydrate_nodes_round_trip():
    """full-node를 draft로 역변환하면 templateId와 가변 슬롯이 추출된다(provider 슬롯 제외)."""
    from core.template_registry import dehydrate_nodes
    drafts = dehydrate_nodes(FULL_NODES)
    assert drafts[0] == {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}}
    assert drafts[1]["templateId"] == "ai.web_search"
    assert drafts[1]["slots"]["prompt"] == "처리해줘"
    assert "llmProvider" not in drafts[1]["slots"]  # provider 슬롯은 제외


def test_strip_invalid_webhook_credentials():
    """보유 자격증명에 없는 webhookCredentialId는 제거되고, 유효한 것은 보존된다(도구 자체는 유지)."""
    from core.workflow_chat import _strip_invalid_webhook_credentials
    nodes = [
        {"id": "node-2", "type": "AI", "config": {"tools": [
            {"name": "discord", "config": {"webhookCredentialId": "wh-1"}},
            {"name": "slack", "config": {"webhookCredentialId": "hallucinated"}},
        ]}},
    ]
    _strip_invalid_webhook_credentials(nodes, {"wh-1"})
    tools = nodes[0]["config"]["tools"]
    assert tools[0] == {"name": "discord", "config": {"webhookCredentialId": "wh-1"}}
    assert tools[1] == {"name": "slack", "config": {}}  # 환각 id 제거, 도구 유지


@pytest.mark.asyncio
async def test_chat_workflow_mcp_assigned_when_catalog_available():
    """보유 MCP 카탈로그가 주입되면 ai.mcp 노드가 하이드레이션되어 검증을 통과한다."""
    payload = json.dumps({
        "message": "생성", "type": "WORKFLOW_GENERATED", "actions": [],
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.mcp", "slots": {"label": "MCP 처리", "prompt": "처리", "catalogId": "cat-abc"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(payload)
    with p1, p2, p3, p4:
        result = await _call(
            prompt="MCP 워크플로우",
            available_mcp_servers=[{"catalogId": "cat-abc", "name": "내 MCP", "description": "테스트"}],
        )
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert {"name": "mcp", "config": {"catalogId": "cat-abc"}} in result.nodes[1].config["tools"]


@pytest.mark.asyncio
async def test_chat_workflow_mcp_rejected_when_no_catalog():
    """카탈로그 미보유 시 ai.mcp의 catalogId가 검증을 통과 못해 CLARIFICATION_NEEDED로 폴백한다."""
    payload = json.dumps({
        "message": "생성", "type": "WORKFLOW_GENERATED", "actions": [],
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.mcp", "slots": {"label": "처리", "prompt": "처리", "catalogId": "nonexistent"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(payload)
    with p1, p2, p3, p4:
        result = await _call(prompt="MCP 워크플로우")
    assert result.type == ChatResponseType.CLARIFICATION_NEEDED


@pytest.mark.asyncio
async def test_chat_workflow_webhook_assigned_when_credential_available():
    """보유 웹훅 자격증명이 주입되면 discord 노드에 webhookCredentialId가 배정되고 검증을 통과한다."""
    payload = json.dumps({
        "message": "생성", "type": "WORKFLOW_GENERATED", "actions": [],
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.discord_send",
             "slots": {"label": "디스코드 발송", "prompt": "발송", "webhookCredentialId": "wh-1"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(payload)
    with p1, p2, p3, p4:
        result = await _call(
            prompt="디스코드 발송 워크플로우",
            available_webhooks=[{"webhookCredentialId": "wh-1", "provider": "DISCORD", "displayName": "내 채널"}],
        )
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert {"name": "discord", "config": {"webhookCredentialId": "wh-1"}} in result.nodes[1].config["tools"]


@pytest.mark.asyncio
async def test_chat_workflow_webhook_hallucinated_credential_stripped():
    """보유하지 않은 webhookCredentialId는 제거되어 하드 실패 없이 생성된다(도구는 유지)."""
    payload = json.dumps({
        "message": "생성", "type": "WORKFLOW_GENERATED", "actions": [],
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거"}},
            {"id": "node-2", "templateId": "ai.discord_send",
             "slots": {"label": "발송", "prompt": "발송", "webhookCredentialId": "hallucinated"}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(payload)
    with p1, p2, p3, p4:
        result = await _call(prompt="디스코드 발송 워크플로우")
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes[1].config["tools"] == [{"name": "discord", "config": {}}]


def test_format_webhook_catalog():
    """format_webhook_catalog가 보유 목록을 instruction 텍스트로 포맷한다(없으면 빈 문자열)."""
    from core.skill_loader import format_webhook_catalog
    assert format_webhook_catalog(None) == ""
    assert format_webhook_catalog([]) == ""
    text = format_webhook_catalog([
        {"webhookCredentialId": "wh-1", "provider": "SLACK", "displayName": "팀채널"},
    ])
    assert "wh-1" in text and "SLACK" in text and "webhookCredentialId" in text


# ── usage 집계 (IEUM-AI-48) ──────────────────────────────────────────────

def _make_patches_with_usage(text_output: str, token_pairs: list | None = None):
    """Runner에 전달된 plugins를 캡처하고, run_async마다 after_model_callback을 호출해
    실제 누적 경로를 태우는 패치 셋.

    Runner를 통째로 mock하면 ADK가 플러그인 콜백을 부르지 않으므로, 여기서 직접 부른다.
    token_pairs는 run_async 호출 순서대로 (prompt, completion) 토큰을 준다.
    반환된 captured["plugins"]로 designer/reviewer가 같은 인스턴스를 공유하는지 검증한다.
    """
    captured = {"plugins": []}
    pairs = token_pairs or []
    state = {"i": 0}

    def _runner_factory(**kwargs):
        plugins = kwargs.get("plugins") or []
        captured["plugins"].append(plugins)

        async def mock_run_async(**_kw):
            i = state["i"]
            state["i"] += 1
            if i < len(pairs):
                prompt_t, completion_t = pairs[i]
                for plugin in plugins:
                    um = MagicMock()
                    um.prompt_token_count = prompt_t
                    um.candidates_token_count = completion_t
                    um.total_token_count = prompt_t + completion_t
                    llm_response = MagicMock()
                    llm_response.usage_metadata = um
                    await plugin.after_model_callback(
                        callback_context=MagicMock(), llm_response=llm_response
                    )
            ev = MagicMock()
            ev.is_final_response.return_value = True
            ev.content.parts = [type("Part", (), {"text": text_output})()]
            yield ev

        runner = MagicMock()
        runner.run_async = mock_run_async
        return runner

    mock_session = AsyncMock()
    mock_session.id = "test-session"
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.delete_session = AsyncMock(return_value=None)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    patches = (
        patch("core.workflow_chat.Runner", side_effect=_runner_factory),
        patch("core.workflow_chat._get_session_service", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    )
    return patches, captured


@pytest.mark.asyncio
async def test_chat_workflow_usage_응답에_채워진다():
    """LLM 호출의 토큰이 ChatResponse.usage에 실린다."""
    (p1, p2, p3, p4), _ = _make_patches_with_usage(
        WORKFLOW_GENERATED_JSON, token_pairs=[(100, 30)]
    )
    with p1, p2, p3, p4:
        result = await _call("워크플로우 만들어줘")

    assert result.usage is not None
    assert result.usage.promptTokens == 100
    assert result.usage.completionTokens == 30
    assert result.usage.totalTokens == 130


@pytest.mark.asyncio
async def test_chat_workflow_usage_designer_reviewer_합산():
    """designer와 reviewer가 같은 플러그인 인스턴스를 공유해 토큰이 합산된다."""
    (p1, p2, p3, p4), captured = _make_patches_with_usage(
        WORKFLOW_GENERATED_JSON, token_pairs=[(100, 30), (50, 20)]
    )
    with p1, p2, p3, p4:
        result = await _call("워크플로우 만들어줘")

    # Runner가 2개(designer/reviewer) 생성되고 둘 다 같은 플러그인 인스턴스를 받아야 한다
    assert len(captured["plugins"]) >= 2
    first = captured["plugins"][0][0]
    assert all(plugins[0] is first for plugins in captured["plugins"])

    # run_async가 2회 이상 돌았다면 합산돼야 한다
    assert result.usage is not None
    assert result.usage.promptTokens >= 100
    assert result.usage.totalTokens >= 130


@pytest.mark.asyncio
async def test_chat_workflow_usage_토큰없으면_None():
    """LLM이 토큰을 보고하지 않으면 usage는 None이다(execute 경로와 동일)."""
    (p1, p2, p3, p4), _ = _make_patches_with_usage(WORKFLOW_GENERATED_JSON, token_pairs=[])
    with p1, p2, p3, p4:
        result = await _call("워크플로우 만들어줘")

    assert result.usage is None
