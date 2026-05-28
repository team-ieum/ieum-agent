import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from api.schemas.chat import ChatResponseType
from core.workflow_chat import chat_workflow

VALID_NODES = [
    {"id": "node-1", "type": "TRIGGER", "label": "트리거",
     "config": {"triggerType": "MANUAL"}},
    {"id": "node-2", "type": "AI", "label": "AI 처리",
     "config": {"llmProvider": "CLAUDE", "credentialId": "",
                "prompt": "처리해줘", "agentType": "simple", "tools": []}},
]
VALID_EDGES = [{"source": "node-1", "target": "node-2", "conditionType": None}]

WORKFLOW_GENERATED_JSON = json.dumps({
    "message": "워크플로우를 생성했습니다.",
    "type": "WORKFLOW_GENERATED",
    "actions": [],
    "changeDescription": None,
    "nodes": VALID_NODES,
    "edges": VALID_EDGES,
    "workflowName": "IT 트렌드 자동 노션 요약",
})

WORKFLOW_MODIFIED_JSON = json.dumps({
    "message": "워크플로우를 수정했습니다.",
    "type": "WORKFLOW_MODIFIED",
    "actions": [],
    "changeDescription": "node-2 프롬프트를 수정했습니다.",
    "nodes": VALID_NODES,
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

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    return (
        patch("core.workflow_chat.Runner", return_value=_make_runner_mock(text_output)),
        patch("core.workflow_chat.InMemorySessionService", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    )


async def _call(prompt="테스트", current_nodes=None, current_edges=None,
                available=None, unavailable=None, preserve_id=None):
    return await chat_workflow(
        prompt=prompt,
        provider="CLAUDE",
        api_key="test-key",
        user_id="test-user",
        available_integrations=available or [],
        unavailable_integrations=unavailable or [],
        current_nodes=current_nodes,
        current_edges=current_edges,
        preserve_id=preserve_id,
    )


@pytest.mark.asyncio
async def test_chat_workflow_신규생성_정상():
    """정상 WORKFLOW_GENERATED 응답이 파싱된다."""
    p1, p2, p3, p4 = _make_patches(WORKFLOW_GENERATED_JSON)
    with p1, p2, p3, p4:
        result = await _call("워크플로우 만들어줘")
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert len(result.nodes) == 2
    assert result.nodes[0].type == "TRIGGER"
    assert len(result.edges) == 1
    assert result.rawPrompt == "워크플로우 만들어줘"
    assert result.workflowName == "IT 트렌드 자동 노션 요약"


@pytest.mark.asyncio
async def test_chat_workflow_수정_정상():
    """currentNodes 전달 시 WORKFLOW_MODIFIED, changeDescription이 존재한다."""
    p1, p2, p3, p4 = _make_patches(WORKFLOW_MODIFIED_JSON)
    with p1, p2, p3, p4:
        result = await _call(
            "프롬프트 수정해줘",
            current_nodes=VALID_NODES,
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


@pytest.mark.asyncio
async def test_chat_workflow_빈_응답_에러():
    """LLM이 빈 응답을 반환하면 ValueError가 발생한다."""
    p1, p2, p3, p4 = _make_patches("")
    with p1, p2, p3, p4:
        with pytest.raises(ValueError):
            await _call()


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
async def test_chat_workflow_invalid_node_type():
    """유효하지 않은 node type → ValueError."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {"triggerType": "MANUAL"}},
            {"id": "node-2", "type": "INVALID_TYPE",
             "label": "잘못된 노드", "config": {}},
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        with pytest.raises(ValueError):
            await _call(preserve_id=True)


@pytest.mark.asyncio
async def test_chat_workflow_duplicate_node_id():
    """중복된 node id → ValueError."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {}},
            {"id": "node-1", "type": "AI", "label": "중복", "config": {}},
        ],
        "edges": [],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        with pytest.raises(ValueError):
            await _call(preserve_id=True)


@pytest.mark.asyncio
async def test_chat_workflow_invalid_edge_reference():
    """존재하지 않는 node를 참조하는 edge → ValueError."""
    invalid_json = json.dumps({
        "message": "생성했습니다.",
        "type": "WORKFLOW_GENERATED",
        "actions": [],
        "changeDescription": None,
        "nodes": [
            {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {}},
        ],
        "edges": [
            {"source": "node-1", "target": "node-999", "conditionType": None},
        ],
    })
    p1, p2, p3, p4 = _make_patches(invalid_json)
    with p1, p2, p3, p4:
        with pytest.raises(ValueError):
            await _call(preserve_id=True)


@pytest.mark.asyncio
async def test_chat_workflow_generated_nodes_없으면_에러():
    """WORKFLOW_GENERATED 타입인데 nodes가 None이면 ValueError가 발생한다."""
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
        with pytest.raises(ValueError):
            await _call()


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
            {"id": "trigger_node", "type": "TRIGGER", "label": "트리거", "config": {"interval": "daily"}},
            {"id": "ai_node", "type": "AI", "label": "AI 처리", "config": {"prompt": "이전 데이터: {{nodes.trigger_node.output.data}}", "agentType": "react", "llmProvider": "GEMINI"}}
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
    
    # 템플릿 참조 변수 변경 검증
    assert result.nodes[1].config["prompt"] == "이전 데이터: {{nodes.node-1.output.data}}"


@pytest.mark.asyncio
async def test_chat_workflow_session_persistence():
    """_get_session_service가 싱글톤으로 동일 user_id에 대해 동일 세션 객체를 반환하고 유지하는지 검증한다."""
    import core.workflow_chat
    core.workflow_chat._SESSION_SERVICE = None
    
    session_service = core.workflow_chat._get_session_service()
    test_user_id = "test-user-persistence-123"
    
    # 1. 첫 번째로 세션 생성
    sess1 = await session_service.create_session(
        app_name="ieum-agent",
        user_id=test_user_id,
        session_id=test_user_id,
    )
    assert sess1 is not None
    assert sess1.id == test_user_id
    
    # 2. 두 번째로 get_session을 호출하여 동일 세션 조회
    sess2 = await session_service.get_session(
        app_name="ieum-agent",
        user_id=test_user_id,
        session_id=test_user_id,
    )
    assert sess2 is not None
    assert sess2.id == test_user_id
    assert sess1.id == sess2.id


@pytest.mark.asyncio
async def test_chat_workflow_self_correction_loop():
    """검증 레이어가 결함을 발견했을 때 피드백을 수용하여 자가 교정(Retry)을 거쳐 최종 결과를 반환하는지 검증한다."""
    # 1. 초안: 결함이 있는 디자인
    faulty_nodes = [
        {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "HTTP", "label": "디스코드 전송", "config": {"url": "https://discord..."}}
    ]
    faulty_draft_json = json.dumps({
        "message": "초안 생성",
        "type": "WORKFLOW_GENERATED",
        "nodes": faulty_nodes,
        "edges": []
    })

    # 2. 리뷰어 피드백: isValid = False
    reviewer_feedback_json = json.dumps({
        "isValid": False,
        "feedback": "외부 연동은 절대로 HTTP 노드를 직접 쓰지 마시고, send_discord_webhook 도구가 주입된 AI 노드를 사용하십시오."
    })

    # 3. 최종 교정본: 올바른 디자인
    corrected_nodes = [
        {"id": "node-1", "type": "TRIGGER", "label": "트리거", "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "AI", "label": "디스코드 전송", "config": {"llmProvider": "CLAUDE", "agentType": "react", "tools": ["discord"], "credentialId": ""}}
    ]
    corrected_workflow_json = json.dumps({
        "message": "교정 완료",
        "type": "WORKFLOW_GENERATED",
        "nodes": corrected_nodes,
        "edges": []
    })

    # 순차적으로 응답을 던져줄 list 생성
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

    # 패치 적용
    mock_session = AsyncMock()
    mock_session_service = MagicMock()
    mock_session_service.get_session = AsyncMock(return_value=None)
    mock_session_service.create_session = AsyncMock(return_value=mock_session)

    mock_lock = MagicMock()
    mock_lock.__aenter__ = AsyncMock(return_value=None)
    mock_lock.__aexit__ = AsyncMock(return_value=None)

    patches = [
        patch("core.workflow_chat.Runner", return_value=mock_runner),
        patch("core.workflow_chat.InMemorySessionService", return_value=mock_session_service),
        patch("core.workflow_chat.get_env_lock", return_value=mock_lock),
        patch("core.workflow_chat._save_chat_log", new_callable=AsyncMock),
    ]

    with patches[0], patches[1], patches[2], patches[3]:
        result = await chat_workflow(
            prompt="디스코드 전송 워크플로우 만들어줘",
            provider="CLAUDE",
            api_key="test-key",
            user_id="test-user",
            available_integrations=[],
            unavailable_integrations=[],
        )

    # 3번의 호출이 정상 수행되었고, 최종적으로 자가 교정본(corrected_nodes)이 반환되었는지 확인
    assert call_index == 3
    assert result.type == ChatResponseType.WORKFLOW_GENERATED
    assert result.nodes[1].type == "AI"
    assert "discord" in result.nodes[1].config["tools"]

