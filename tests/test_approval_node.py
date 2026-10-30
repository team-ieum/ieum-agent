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


def test_approval_template_registered():
    tr.validate_registry()
    tpl = tr.get_template("approval.request")
    assert tpl is not None and tpl["node_type"] == "APPROVAL"
    assert tr.resolve_template_for_node({"type": "APPROVAL", "event": "approval.request", "config": {}})["id"] == "approval.request"


def test_workflow_node_schema_accepts_approval():
    """저장된 APPROVAL 노드가 채팅 수정 요청(currentNodes)으로 되돌아와도 422가 나지 않는다."""
    node = WorkflowNode(id="node-2", event="approval.request", label="발송 전 승인",
                        config={"message": "보내도 될까요?"})
    assert node.config == {"message": "보내도 될까요?"}
    assert WorkflowNode(id="node-2", event="approval.request", label="승인", config={}).config == {}


def test_workflow_node_schema_rejects_non_string_message():
    with pytest.raises(ValidationError, match="문자열이어야"):
        WorkflowNode(id="node-2", event="approval.request", label="승인", config={"message": 123})


def test_approval_message_survives_dehydrate_hydrate_round_trip():
    node = hydrate_node({"id": "node-2", "templateId": "approval.request",
                         "slots": {"label": "발송 전 승인",
                                   "description": "보내기 전에 담당자가 확인하고 승인해요.",
                                   "message": "슬랙으로 보내도 될까요?"}})
    assert node == {"id": "node-2", "type": "APPROVAL", "event": "approval.request", "label": "발송 전 승인",
                    "description": "보내기 전에 담당자가 확인하고 승인해요.",
                    "config": {"message": "슬랙으로 보내도 될까요?"}}
    draft = dehydrate_node(node)
    assert draft["templateId"] == "approval.request"
    assert draft["slots"]["message"] == "슬랙으로 보내도 될까요?"
    assert hydrate_node(draft) == node


# --- 생성 가이드: 요청할 때만 넣는다 -------------------------------------------
