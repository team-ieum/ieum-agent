"""APPROVAL(사람 승인 게이트) 노드 계약 (IEUM-BE-45)

BE 런타임이 이 노드에서 실행을 멈추고, 소유자가 승인하면 이어진 실행이 게이트 다음부터 돈다.
agent는 이 노드를 만들고(채팅/생성) 수정 왕복에서 보존만 한다 — 실행하지 않는다.
config는 승인자에게 보여 줄 message 하나(선택)다.
"""
import pytest
from pydantic import ValidationError

from api.schemas.generate_workflow import WorkflowNode
from core import template_registry as tr
from core.template_registry import dehydrate_node, hydrate_node
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError


def _gated_workflow(approval_config: dict):
    """TRIGGER → APPROVAL → AI. AI는 게이트 출력(승인자)을 참조한다."""
    nodes = [
        {"id": "node-1", "type": "TRIGGER", "label": "매일 아침 9시",
         "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},
        {"id": "node-2", "type": "APPROVAL", "label": "발송 전 승인",
         "description": "보내기 전에 담당자가 확인하고 승인해요.", "config": approval_config},
        {"id": "node-3", "type": "AI", "label": "뉴스 수집",
         "config": {"llmProvider": "GEMINI", "agentType": "react",
                    "tools": [{"name": "builtin:web_search"}],
                    "prompt": "{{nodes.node-2.output.approvedBy}} 님이 승인했으니 최신 뉴스를 수집해줘"}},
    ]
    edges = [{"source": "node-1", "target": "node-2"}, {"source": "node-2", "target": "node-3"}]
    return nodes, edges


def test_approval_template_registered():
    tr.validate_registry()
    tpl = tr.get_template("approval")
    assert tpl is not None and tpl["node_type"] == "APPROVAL"
    assert tr.resolve_template_for_node({"type": "APPROVAL", "config": {}})["id"] == "approval"


def test_validator_accepts_gated_workflow_with_message():
    """message가 APPROVAL 화이트리스트에 있음을 고정한다.

    검증기는 참조식의 출력 필드명(approvedBy)을 확인하지 않는다 — 참조 대상 노드가 선행(upstream)에
    존재하는지만 본다. 그래서 이 테스트가 지키는 것은 출력 참조가 아니라 message 허용이다."""
    nodes, edges = _gated_workflow({"message": "슬랙으로 보내도 될까요?"})
    WorkflowValidator.validate(nodes, edges)


def test_validator_rejects_unknown_approval_config_field():
    """템플릿 화이트리스트 밖 필드(승인자 지정 등 범위 밖 기능)는 날조로 보고 거부한다."""
    nodes, edges = _gated_workflow({"message": "확인", "approver": "kim"})
    with pytest.raises(WorkflowValidationError, match="허용되지 않은 필드"):
        WorkflowValidator.validate(nodes, edges)


def test_workflow_node_schema_accepts_approval():
    """저장된 APPROVAL 노드가 채팅 수정 요청(currentNodes)으로 되돌아와도 422가 나지 않는다."""
    node = WorkflowNode(id="node-2", type="APPROVAL", label="발송 전 승인",
                        config={"message": "보내도 될까요?"})
    assert node.config == {"message": "보내도 될까요?"}
    assert WorkflowNode(id="node-2", type="APPROVAL", label="승인", config={}).config == {}


def test_workflow_node_schema_rejects_non_string_message():
    with pytest.raises(ValidationError, match="문자열이어야"):
        WorkflowNode(id="node-2", type="APPROVAL", label="승인", config={"message": 123})


def test_approval_message_survives_dehydrate_hydrate_round_trip():
    node = hydrate_node({"id": "node-2", "templateId": "approval",
                         "slots": {"label": "발송 전 승인",
                                   "description": "보내기 전에 담당자가 확인하고 승인해요.",
                                   "message": "슬랙으로 보내도 될까요?"}})
    assert node == {"id": "node-2", "type": "APPROVAL", "label": "발송 전 승인",
                    "description": "보내기 전에 담당자가 확인하고 승인해요.",
                    "config": {"message": "슬랙으로 보내도 될까요?"}}
    draft = dehydrate_node(node)
    assert draft["templateId"] == "approval"
    assert draft["slots"]["message"] == "슬랙으로 보내도 될까요?"
    assert hydrate_node(draft) == node
