"""노드 description(사용자용 자연어 설명) 회귀 가드 (IEUM-AI-55 Task 1)

FE 노드 카드가 표시할 description은 agent가 넣지 않으면 backend가 채워주지 않는다
(ChatService가 agent 응답 노드를 그대로 저장). 따라서 '모든 템플릿이 필수 슬롯으로 갖는다'와
'하이드레이션/역하이드레이션에서 살아남는다'를 코드로 강제한다.
"""
import pytest

from core import template_registry as tr
from core.template_registry import SlotFillError, hydrate_node, dehydrate_node
from api.schemas.generate_workflow import WorkflowNode

# description에 새어 나오면 안 되는 개발자 시점 토큰(사용자에게 그대로 보인다).
_TECHNICAL_TOKENS = ["{{", "templateId", "builtin:", "config.", "node_type",
                     "JSON", "slot", "output.output"]


def _description_slot(tpl: dict) -> dict | None:
    return next((s for s in tpl["slots"] if s["name"] == "description"), None)


def test_every_template_has_required_description_slot():
    """템플릿 전수(28개)가 필수 description 슬롯을 갖는다."""
    templates = tr.all_templates()
    assert len(templates) == 28, f"템플릿 개수가 바뀌었다: {len(templates)}"
    for tpl in templates:
        slot = _description_slot(tpl)
        assert slot is not None, f"{tpl['id']}: description 슬롯 없음"
        assert slot["path"] == "description", f"{tpl['id']}: description 슬롯 path 오류"
        assert slot["kind"] == "string", f"{tpl['id']}: description 슬롯 kind 오류"
        assert slot["required"] is True, f"{tpl['id']}: description 슬롯이 필수가 아님"


def test_description_slot_appears_in_slot_catalog_as_required():
    """Builder/Designer에 주입되는 슬롯 카탈로그에 description(필수)이 노출된다."""
    catalog = tr.slot_catalog_text()
    slot_lines = [ln for ln in catalog.split("\n") if ln.strip().startswith("slots:")]
    # 카탈로그 포맷이 바뀌어 매칭 줄이 0개가 되면 아래 루프가 조용히 통과한다(공허한 참).
    assert len(slot_lines) == len(tr.all_templates()), f"슬롯 줄 파싱 실패: {len(slot_lines)}개"
    for line in slot_lines:
        assert "description(string,필수)" in line, f"카탈로그에 필수 description 누락: {line}"


def test_every_golden_snippet_has_user_facing_description():
    """골든 스니펫의 description은 비어 있지 않고 기술 용어가 없는 사용자용 문장이다.
    (LLM이 스니펫 톤을 모방하므로 개발자 시점 문장이 섞이면 그대로 사용자에게 노출된다.)"""
    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet")
        if not snip:
            continue
        desc = snip.get("description")
        assert desc and desc.strip(), f"{tpl['id']}: 골든 스니펫 description 누락"
        for token in _TECHNICAL_TOKENS:
            assert token not in desc, f"{tpl['id']}: description에 기술 용어 '{token}' 포함 — {desc}"


def test_hydrate_sets_top_level_description():
    """description 슬롯은 config가 아니라 노드 최상위에 채워진다(FE 계약)."""
    node = hydrate_node({
        "id": "node-1", "templateId": "trigger.manual",
        "slots": {"label": "시작", "description": "버튼을 누르면 시작돼요."},
    })
    assert node["description"] == "버튼을 누르면 시작돼요."
    assert "description" not in node["config"]
    assert WorkflowNode(**node).description == "버튼을 누르면 시작돼요."


@pytest.mark.parametrize("template_id,slots", [
    ("trigger.manual", {"label": "시작"}),
    ("condition", {"label": "분기", "operator": "equals",
                   "leftValue": "{{nodes.node-1.output.output}}", "rightValue": "x"}),
    ("ai.reasoning", {"label": "요약", "prompt": "요약해줘"}),
])
def test_hydrate_rejects_draft_without_description(template_id, slots):
    """description을 빠뜨린 draft는 하이드레이션에서 거부된다(=생성 응답에 빈 description 불가).
    생성/채팅 경로는 이 오류를 LLM에 되먹여 재작성시킨다."""
    with pytest.raises(SlotFillError, match="description"):
        hydrate_node({"templateId": template_id, "slots": slots}, provider="CLAUDE")


def test_dehydrate_preserves_description_for_modify():
    """MODIFY 역변환(full-node → draft)에서 description이 슬롯으로 보존된다."""
    node = {
        "id": "node-2", "type": "AI", "label": "요약",
        "description": "AI가 앞의 결과를 읽고 짧게 정리해요.",
        "config": {"agentType": "simple", "credentialId": "", "tools": [],
                   "llmProvider": "CLAUDE", "prompt": "요약해줘"},
    }
    draft = dehydrate_node(node)
    assert draft["slots"]["description"] == "AI가 앞의 결과를 읽고 짧게 정리해요."
    # 왕복: 다시 하이드레이션해도 동일한 description이 유지된다.
    assert hydrate_node(draft, provider="CLAUDE")["description"] == node["description"]


def test_workflow_node_description_defaults_for_legacy_payload():
    """description 도입 이전에 저장된 노드가 수정/채팅 요청으로 되돌아와도 422가 아니다."""
    node = WorkflowNode(id="node-1", type="TRIGGER", label="시작",
                        config={"triggerType": "MANUAL"})
    assert node.description == ""
