"""CONDITION config 키 표기 통일(IEUM-AI-55 Task 3).

FE 명세·BE executor가 보는 이름은 left/right인데 agent는 leftValue/rightValue를 만들어
FE 기술정보 칸이 비었다. agent는 이제 left/right만 만든다.
"""
import pytest

import core.template_registry as tr
from core.template_registry import SlotFillError, hydrate_node

_LEFT_EXPR = "{{nodes.node-1.output.output}}"
CANONICAL_CFG = {"operator": "equals", "left": _LEFT_EXPR, "right": "urgent"}


def _condition_draft(**slots) -> dict:
    base = {"label": "분기", "description": "앞의 결과에 따라 갈 길을 나눠요."}
    base.update(slots)
    return {"id": "node-2", "templateId": "branch.if", "slots": base}


# ── 템플릿(SSOT) ────────────────────────────────────────────────────────────

def test_condition_template_uses_canonical_keys():
    tpl = tr.get_template("branch.if")
    by_name = {s["name"]: s for s in tpl["slots"]}
    assert by_name["left"]["path"] == "config.left"
    assert by_name["right"]["path"] == "config.right"
    assert by_name["left"]["required"] and by_name["right"]["required"]
    assert set(tpl["allowed_config_fields"]) == {"operator", "left", "right"}
    assert set(tpl["golden_snippet"]["config"]) == {"operator", "left", "right"}


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
