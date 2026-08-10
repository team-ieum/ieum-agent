import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.workflow_modifier import modify_workflow
from core.template_registry import (
    hydrate_node, dehydrate_node, load_templates,
    PASSTHROUGH_TEMPLATE_ID, _get_by_path, _SYSTEM_INJECTED_KINDS,
)
from tools.registry import apply_service_brand


def _slots_from_golden(tpl: dict) -> dict:
    """템플릿 golden_snippet에서 non-system 슬롯 값을 뽑는다(테스트 fixture 생성용)."""
    slots = {}
    for s in tpl["slots"]:
        if s["kind"] in _SYSTEM_INJECTED_KINDS:
            continue
        val = _get_by_path(tpl["golden_snippet"], s["path"])
        if val not in (None, ""):
            slots[s["name"]] = val
    return slots


def _build_node(template_id: str, node_id: str, provider: str = "CLAUDE", **slot_overrides) -> dict:
    """templateId의 golden_snippet 슬롯으로 완성 노드를 만든다(override로 일부 덮어씀)."""
    tpl = load_templates()[template_id]
    slots = {**_slots_from_golden(tpl), **slot_overrides}
    node = hydrate_node({"id": node_id, "templateId": template_id, "slots": slots}, provider=provider)
    return node


VALID_MODIFY_JSON = json.dumps({
    "nodes": [
        {"id": "node-1", "templateId": "trigger.schedule",
         "slots": {"label": "매일 오전 9시", "description": "매일 아침 9시에 자동으로 시작돼요.", "cron": "0 9 * * *"}},
        {"id": "node-2", "templateId": "ai.reasoning",
         "slots": {"label": "뉴스 요약", "description": "AI가 뉴스를 읽고 요약해요.", "prompt": "요약해줘"}},
        {"id": "node-3", "templateId": "ai.slack_send",
         "slots": {"label": "Slack 알림", "description": "정리된 내용을 슬랙으로 보내드려요.",
                    "prompt": "다음 내용을 슬랙으로 보내줘: {{nodes.node-2.output.output}}"}},
    ],
    "edges": [
        {"source": "node-1", "target": "node-2", "conditionType": None},
        {"source": "node-2", "target": "node-3", "conditionType": None},
    ],
    "changeDescription": "Slack 알림 노드(node-3)를 node-2 다음에 추가했습니다."
})

CURRENT_NODES = [
    {"id": "node-1", "type": "TRIGGER", "label": "매일 오전 9시", "description": "매일 아침 9시에 자동으로 시작돼요.",
     "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},
    {"id": "node-2", "type": "AI", "label": "뉴스 요약", "description": "AI가 뉴스를 읽고 요약해요.",
     "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "요약해줘", "agentType": "simple", "tools": []}},
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
async def test_modify_workflow_정상_draft_파싱():
    """유효한 수정 결과 draft JSON을 반환하면 하이드레이션되어 ModifyWorkflowResponse로 파싱된다."""
    p1, p2, p3, p4 = _make_patches(VALID_MODIFY_JSON)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "Slack 알림 노드를 추가해줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
        )

    assert len(result.nodes) == 3
    assert result.nodes[0].type == "TRIGGER"
    assert result.nodes[2].label == "Slack 알림"
    # model·serviceType은 슬롯이 아니라 하이드레이션이 템플릿에서 결정론적으로 채운다.
    assert result.nodes[2].config["serviceType"] == "SLACK"
    assert result.nodes[2].config["model"]
    assert result.nodes[2].config["brand"] == "slack"
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
async def test_modify_workflow_nodes_없으면_파싱_에러():
    """nodes 부재를 빈 리스트로 흡수하면 200 + 노드 0개가 나가 원본이 통째로 날아간다."""
    payload = {"edges": [], "changeDescription": "슬랙 노드를 디스코드로 바꿨습니다"}
    p1, p2, p3, p4 = _make_patches(json.dumps(payload))
    with p1, p2, p3, p4:
        with pytest.raises(Exception):
            await modify_workflow(
                "바꿔줘", CURRENT_NODES, CURRENT_EDGES, "CLAUDE", "test-key",
            )


@pytest.mark.asyncio
async def test_modify_workflow_레거시_빈_description_label로_채움():
    """description 슬롯 도입 이전 저장분(레거시)은 draft에서 description이 아예 빠진다. LLM이
    그대로 복사해 보내도(2번 규칙) 하이드레이션 직전에 label로 보정되어 실패하지 않는다."""
    legacy_nodes = [
        {"id": "node-1", "type": "TRIGGER", "label": "매일 오전 9시",
         "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},  # description 키 자체가 없음
    ]
    llm_output = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.schedule", "slots": {"label": "매일 오전 9시", "cron": "0 9 * * *"}},
        ],
        "edges": [],
        "changeDescription": "변경 없음",
    })
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "그대로 둬", legacy_nodes, [], "CLAUDE", "test-key",
        )
    assert result.nodes[0].description == "매일 오전 9시"


@pytest.mark.asyncio
async def test_modify_workflow_신규_노드_description_누락시_거부():
    """description 슬롯 자동 복원은 레거시 id에만 적용된다. 신규 노드가 description을 빠뜨리면
    (구 _preserve_descriptions처럼 label로 조용히 채우지 않고) 하드 실패한다 — 노드가 무슨 앱인지
    조용히 되채우려던 시도가 IEUM-AI-55 회귀의 근원이었다."""
    llm_output = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
            {"id": "node-2", "templateId": "ai.slack_send", "slots": {"label": "Slack 알림", "prompt": "보내줘"}},  # description 누락, 신규 노드
        ],
        "edges": [{"source": "node-1", "target": "node-2", "conditionType": None}],
        "changeDescription": "Slack 노드를 추가했습니다.",
    })
    current_nodes = [
        {"id": "node-1", "type": "TRIGGER", "label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요.",
         "config": {"triggerType": "MANUAL"}},
    ]
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        with pytest.raises(ValueError, match="파싱에 실패했습니다"):
            await modify_workflow(
                "Slack 알림 노드를 추가해줘", current_nodes, [], "CLAUDE", "test-key",
            )


# ── pass-through draft (템플릿 매칭 실패 노드) ──────────────────────────────

# 어떤 템플릿과도 매칭되지 않는 AI 노드(builtin:json_parse만 가진 노드) → dehydrate 시 pass-through draft.
PASSTHROUGH_NODE = {
    "id": "node-2", "type": "AI", "label": "가공", "description": "이 노드가 하는 일을 쉽게 설명해요.",
    "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "파싱해줘",
               "agentType": "react", "tools": [{"name": "builtin:json_parse"}]},
}
TRIGGER_NODE = {
    "id": "node-1", "type": "TRIGGER", "label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요.",
    "config": {"triggerType": "MANUAL"},
}


@pytest.mark.asyncio
async def test_modify_workflow_passthrough_node_survives_round_trip():
    """템플릿 매칭 실패 노드는 수정 왕복에서 사라지지 않는다(LLM이 그대로 복사해 돌려준 경우)."""
    current_nodes = [TRIGGER_NODE, PASSTHROUGH_NODE]
    llm_output = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
            dehydrate_node(PASSTHROUGH_NODE),
        ],
        "edges": [],
        "changeDescription": "변경 없음",
    })
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "그대로 둬", current_nodes, [], "CLAUDE", "test-key",
        )
    assert len(result.nodes) == 2
    node2 = next(n for n in result.nodes if n.id == "node-2")
    assert node2.config["prompt"] == "파싱해줘"
    assert node2.config["tools"] == [{"name": "builtin:json_parse"}]


@pytest.mark.asyncio
async def test_modify_workflow_passthrough_forged_content_ignored():
    """LLM이 pass-through draft의 'node' 필드를 조작해도, 서버가 쥔 current_nodes 원본으로 강제 치환된다."""
    current_nodes = [TRIGGER_NODE, PASSTHROUGH_NODE]
    forged_draft = {
        "id": "node-2", "templateId": PASSTHROUGH_TEMPLATE_ID,
        "node": {"id": "node-2", "type": "AI",
                  "config": {"llmProvider": "CLAUDE", "credentialId": "hacked-cred",
                             "agentType": "react", "tools": [{"name": "builtin:json_parse"}],
                             "prompt": "완전히 다른 프롬프트로 날조", "extraField": "환각 필드"}}}
    llm_output = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
            forged_draft,
        ],
        "edges": [],
        "changeDescription": "가공 노드를 손봤습니다.",
    })
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "가공 노드 손봐줘", current_nodes, [], "CLAUDE", "test-key",
        )
    node2 = next(n for n in result.nodes if n.id == "node-2")
    assert node2.config["credentialId"] == ""
    assert node2.config["prompt"] == "파싱해줘"
    assert "extraField" not in node2.config


@pytest.mark.asyncio
async def test_modify_workflow_passthrough_new_node_forgery_rejected():
    """current_nodes에 없는 id를 pass-through draft로 날조해 신규 노드를 만들려는 시도는 거부된다."""
    current_nodes = [TRIGGER_NODE]
    llm_output = json.dumps({
        "nodes": [
            {"id": "node-1", "templateId": "trigger.manual", "slots": {"label": "트리거", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
            {"id": "node-9", "templateId": PASSTHROUGH_TEMPLATE_ID,
             "node": {"id": "node-9", "type": "AI",
                      "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "날조된 신규 노드",
                                 "agentType": "simple", "tools": []}}},
        ],
        "edges": [],
        "changeDescription": "노드를 추가했습니다.",
    })
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        with pytest.raises(ValueError, match="파싱에 실패했습니다"):
            await modify_workflow(
                "노드 추가해줘", current_nodes, [], "CLAUDE", "test-key",
            )


# ── 슬롯 밖 값 재부착(reattach) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_modify_workflow_keeps_stored_credential_empty():
    """저장분에 credentialId가 UUID로 남아 있어도 수정 결과는 ''다(런타임 주입 계약).
    되살리면 WorkflowValidator가 거부해 그 워크플로우의 수정이 영구히 불가능해진다."""
    stored_node = {
        "id": "node-1", "type": "AI", "label": "슬랙 알림", "description": "정리된 내용을 슬랙으로 보내드려요.",
        "config": {"llmProvider": "CLAUDE", "credentialId": "42a230ce-32a4-4f99-aaed-da00dc85c2a8",
                   "prompt": "보내줘", "agentType": "react", "tools": [{"name": "slack"}]},
    }
    draft = dehydrate_node(stored_node)
    llm_output = json.dumps({"nodes": [draft], "edges": [], "changeDescription": "변경 없음"})

    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "그대로 둬", [stored_node], [], "CLAUDE", "test-key",
        )
    assert result.nodes[0].config["credentialId"] == ""


# ── 생성 경로와 수정 경로의 model·serviceType·brand 일치 ─────────────────────

@pytest.mark.parametrize("template_id,label,prompt", [
    ("ai.slack_send", "슬랙 알림 발송", "다음 PR 요약을 개발 채널에 슬랙 메시지로 보내줘: {{nodes.node-3.output.prSummary}}"),
    ("ai.github_query", "GitHub PR 목록 조회",
     "team-ieum/ieum-backend의 PR을 최신순 1페이지(per_page=30, page=1)만 조회해줘."),
    ("ai.reasoning", "결과 요약", "{{nodes.node-2.output.output}}를 3문장 이내로 요약해줘."),
])
@pytest.mark.asyncio
async def test_modify_workflow_matches_generate_path_tech_fields(template_id, label, prompt):
    """같은 노드를 생성 경로(hydrate_node+apply_service_brand)와 수정 경로(modify_workflow)로 각각
    만들면 model·serviceType·brand가 같은 값이다(단일 도구·무도구 GitHub·순수 추론 세 케이스)."""
    tpl = load_templates()[template_id]
    slots = {**_slots_from_golden(tpl), "label": label, "prompt": prompt}

    # 생성 경로: hydrate_node 직접 호출 + apply_service_brand (core.workflow_generator와 동일 절차)
    generated = hydrate_node({"id": "node-1", "templateId": template_id, "slots": slots}, provider="CLAUDE")
    apply_service_brand([generated])

    # 수정 경로: 동일 draft를 current_nodes(생성 결과)로 두고 LLM이 그대로 돌려준 상황을 재현
    current_nodes = [generated]
    llm_output = json.dumps({
        "nodes": [dehydrate_node(generated)],
        "edges": [],
        "changeDescription": "변경 없음",
    })
    p1, p2, p3, p4 = _make_patches(llm_output)
    with p1, p2, p3, p4:
        result = await modify_workflow(
            "그대로 둬", current_nodes, [], "CLAUDE", "test-key",
        )

    modified = result.nodes[0]
    assert modified.config.get("model") == generated["config"].get("model")
    assert modified.config.get("serviceType") == generated["config"].get("serviceType")
    assert modified.config.get("brand") == generated["config"].get("brand")
