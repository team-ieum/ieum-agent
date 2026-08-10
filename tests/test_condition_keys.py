"""CONDITION config 키 표기 통일(IEUM-AI-55 Task 3).

FE 명세·BE executor가 보는 이름은 left/right인데 agent는 leftValue/rightValue를 만들어
FE 기술정보 칸이 비었다. agent는 이제 left/right만 만들고, 구표기로 저장된 워크플로우가
수정 요청으로 되돌아오면 스키마 관문에서 표준 표기로 옮긴다.
"""
import glob
import json
import os

import pytest

import core.template_registry as tr
from api.schemas.chat import ChatRequest
from api.schemas.generate_workflow import WorkflowNode
from core.template_registry import SlotFillError, dehydrate_node, hydrate_node
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError

_LEFT_EXPR = "{{nodes.node-1.output.output}}"
CANONICAL_CFG = {"operator": "equals", "left": _LEFT_EXPR, "right": "urgent"}
LEGACY_CFG = {"operator": "equals", "leftValue": _LEFT_EXPR, "rightValue": "urgent"}


def _condition_node(config: dict) -> dict:
    """저장된 full-node 포맷의 CONDITION 노드."""
    return {"id": "node-2", "type": "CONDITION", "label": "분기",
            "description": "앞의 결과에 따라 갈 길을 나눠요.", "config": dict(config)}


def _condition_draft(**slots) -> dict:
    base = {"label": "분기", "description": "앞의 결과에 따라 갈 길을 나눠요."}
    base.update(slots)
    return {"id": "node-2", "templateId": "condition", "slots": base}


# ── 템플릿(SSOT) ────────────────────────────────────────────────────────────

def test_condition_template_uses_canonical_keys():
    tpl = tr.get_template("condition")
    by_name = {s["name"]: s for s in tpl["slots"]}
    assert by_name["left"]["path"] == "config.left"
    assert by_name["right"]["path"] == "config.right"
    assert by_name["left"]["required"] and by_name["right"]["required"]
    assert set(tpl["allowed_config_fields"]) == {"operator", "left", "right"}
    assert set(tpl["golden_snippet"]["config"]) == {"operator", "left", "right"}


def test_no_legacy_condition_keys_in_design_assets():
    """설계 자산(템플릿·레퍼런스 문서)과 chat 설계 프롬프트에 구표기가 남아 있지 않다.
    한 곳이라도 남으면 모델이 그대로 따라 써 표기가 다시 갈라진다."""
    from core.workflow_chat import _SYSTEM_PROMPT_BASE

    paths = glob.glob(os.path.join(tr.TEMPLATES_DIR, "*.json"))
    ref_dir = os.path.join(os.path.dirname(tr.TEMPLATES_DIR), "references")
    paths += glob.glob(os.path.join(ref_dir, "*.md"))
    assert paths, "설계 자산 경로 탐색 실패"

    sources = {p: open(p, encoding="utf-8").read() for p in paths}
    sources["_SYSTEM_PROMPT_BASE"] = _SYSTEM_PROMPT_BASE
    offenders = [name for name, text in sources.items()
                 if "leftValue" in text or "rightValue" in text]
    assert not offenders, f"구표기(leftValue/rightValue) 잔존: {offenders}"


# ── 하이드레이션(생성 경로) ────────────────────────────────────────────────

def test_hydrate_condition_emits_canonical_keys_only():
    node = hydrate_node(_condition_draft(operator="equals", left=_LEFT_EXPR, right="urgent"))
    assert node["config"] == CANONICAL_CFG


def test_hydrate_rejects_legacy_slot_names():
    """Designer가 구표기 슬롯을 쓰면 거부된다(생성 응답에 구표기가 다시 섞이지 않는다)."""
    with pytest.raises(SlotFillError, match="leftValue"):
        hydrate_node(_condition_draft(operator="equals", leftValue=_LEFT_EXPR, rightValue="x"))


@pytest.mark.parametrize("missing", ["left", "right", "operator"])
def test_hydrate_requires_each_condition_slot(missing):
    slots = {"operator": "equals", "left": _LEFT_EXPR, "right": "urgent"}
    slots.pop(missing)
    with pytest.raises(SlotFillError, match=missing):
        hydrate_node(_condition_draft(**slots))


def test_validator_rejects_legacy_key_on_node():
    """구표기 키는 화이트리스트 밖이다 — 하류로 흘리면 검증에서 걸린다(스키마 관문 정규화의 근거)."""
    with pytest.raises(WorkflowValidationError, match="leftValue"):
        WorkflowValidator.validate(
            [{"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {"triggerType": "MANUAL"}},
             _condition_node(LEGACY_CFG)],
            [{"source": "node-1", "target": "node-2", "conditionType": None}],
        )


# ── 스키마 검증 + 레거시 정규화 ────────────────────────────────────────────

@pytest.mark.parametrize("missing", ["operator", "left", "right"])
def test_workflow_node_requires_canonical_fields(missing):
    cfg = dict(CANONICAL_CFG)
    cfg.pop(missing)
    with pytest.raises(ValueError, match=missing):
        WorkflowNode(**_condition_node(cfg))


def test_workflow_node_migrates_legacy_keys():
    """구표기로 저장된 노드는 거부되지 않고 표준 표기로 옮겨진다(구표기는 남지 않는다)."""
    node = WorkflowNode(**_condition_node(LEGACY_CFG))
    assert node.config == CANONICAL_CFG


def test_workflow_node_keeps_canonical_when_both_present():
    cfg = {**CANONICAL_CFG, "leftValue": "구값", "rightValue": "구값"}
    node = WorkflowNode(**_condition_node(cfg))
    assert node.config["left"] == _LEFT_EXPR and node.config["right"] == "urgent"
    assert "leftValue" not in node.config and "rightValue" not in node.config


def test_legacy_workflow_is_accepted_at_api_boundary():
    """/v1/chat의 currentNodes로 들어온 레거시 CONDITION이 422로 튕기지 않고,
    라우터가 넘기는 model_dump()에는 표준 표기만 담긴다."""
    node = ChatRequest(prompt="수정", currentNodes=[_condition_node(LEGACY_CFG)],
                       currentEdges=[]).currentNodes[0]
    assert node.model_dump()["config"] == CANONICAL_CFG


def test_legacy_condition_survives_chat_round_trip():
    """레거시 워크플로우 수정 시나리오: API 관문 → dehydrate(draft) → Designer 복사 → hydrate.
    구표기 노드가 draft에서 left/right 슬롯으로 실려야 Designer가 그대로 복사해도 첫 시도에 성공한다."""
    boundary_node = WorkflowNode(**_condition_node(LEGACY_CFG)).model_dump()
    draft = dehydrate_node(boundary_node)
    assert draft["templateId"] == "condition"
    assert draft["slots"]["left"] == _LEFT_EXPR and draft["slots"]["right"] == "urgent"

    rehydrated = hydrate_node(draft)  # Designer가 draft를 그대로 복사해 돌려준 경우
    assert rehydrated["config"] == CANONICAL_CFG


def test_response_nodes_use_canonical_keys():
    """LLM이 현재 워크플로우의 구표기를 그대로 베껴도 응답 노드는 표준 표기다."""
    node = WorkflowNode(**_condition_node(LEGACY_CFG))
    assert json.loads(node.model_dump_json())["config"] == CANONICAL_CFG
