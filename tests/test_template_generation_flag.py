import copy
import json
import os

import pytest

from core import template_registry as tr
from core.template_registry import SlotFillError
from core.validators.plan_validator import PlanValidator, PlanValidationError

# 메모리상 정규화된 모양만 쓴다(파일 형식과 무관) — 생성 경로 필터만 검증한다.
_FAKE = {
    "id": "action.fake_send", "node_type": "ACTION", "tool_key": "slack",
    "tags": ["가짜전송"], "menu": "가짜 전송", "builder": True, "generation": False,
    "fixed": {"type": "ACTION", "config": {"tools": [{"name": "slack"}]}},
    "slots": [], "allowed_config_fields": ["tools"],
}


@pytest.fixture
def with_fake(monkeypatch):
    templates = dict(tr.load_templates())
    templates[_FAKE["id"]] = copy.deepcopy(_FAKE)
    monkeypatch.setattr(tr, "_cache", templates)


def test_builder_only_entry_hidden_from_generation_views(with_fake):
    assert _FAKE["id"] in {t["id"] for t in tr.all_entries()}
    assert _FAKE["id"] not in {t["id"] for t in tr.all_templates()}
    assert _FAKE["id"] not in tr.menu_index()
    assert _FAKE["id"] not in tr.slot_catalog_text()
    assert _FAKE["id"] not in tr.template_ids()
    assert tr.node_type_of_template(_FAKE["id"]) is None
    assert all(t["id"] != _FAKE["id"] for t in tr.select_by_tags("가짜전송 해줘"))


def test_hydrate_rejects_builder_only_template(with_fake):
    with pytest.raises(SlotFillError, match="존재하지 않는 templateId"):
        tr.hydrate_node({"id": "n1", "templateId": _FAKE["id"], "slots": {}}, provider="GEMINI")


def test_plan_validator_rejects_builder_only_template(with_fake):
    from api.schemas.generate_workflow import WorkflowPlanSchema
    plan = WorkflowPlanSchema.model_validate({
        "nodes": [{"id": "node-1", "templateId": "trigger.manual", "role": "시작", "description": "수동 실행"},
                  {"id": "node-2", "templateId": _FAKE["id"], "role": "전송", "description": "가짜 전송"}],
        "edges": [{"source": "node-1", "target": "node-2"}],
        "justification": "테스트",
    })
    with pytest.raises(PlanValidationError, match="templateId 'action.fake_send'"):
        PlanValidator.validate(plan)


def test_action_node_not_matched_for_generation(with_fake):
    node = {"id": "n1", "type": "ACTION", "label": "x", "config": {"tools": [{"name": "slack"}]}}
    assert tr.resolve_template_for_node(node) is None
    assert tr.allowed_config_fields_for_node(node) is None


def test_registry_accepts_action_node_type():
    assert "ACTION" in tr._VALID_NODE_TYPES


def test_generation_flag_must_be_bool():
    path = os.path.join(tr.TEMPLATES_DIR, "trigger.manual.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["generation"] = "no"
    with pytest.raises(tr.TemplateSchemaError, match="generation"):
        tr._validate_template(raw, path, tr._tool_map_keys())
