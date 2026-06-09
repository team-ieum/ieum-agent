import pytest
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.validators.plan_validator import PlanValidator, PlanValidationError


def _plan(nodes, edges=None):
    return WorkflowPlanSchema(nodes=nodes, edges=edges or [], justification="테스트 근거")


TRIGGER = {"id": "node-1", "templateId": "trigger.manual", "role": "트리거", "description": "수동 시작"}


# --- 기본 구조 검증 ---
def test_valid_plan_passes():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "templateId": "ai.notion_search", "role": "검색", "description": "노션 검색"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    PlanValidator.validate(plan)


def test_unknown_template_id_raises():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "templateId": "ai.does_not_exist", "role": "x", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError) as excinfo:
        PlanValidator.validate(plan)
    assert "존재하지 않습니다" in str(excinfo.value)


def test_missing_trigger_raises():
    plan = _plan([{"id": "node-2", "templateId": "ai.notion_search", "role": "x", "description": "d"}])
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_first_node_must_be_trigger():
    plan = _plan(
        [{"id": "node-2", "templateId": "ai.notion_search", "role": "x", "description": "d"}, TRIGGER],
        [{"source": "node-2", "target": "node-1"}],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_multiple_triggers_raises():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "templateId": "trigger.schedule", "role": "t2", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_duplicate_node_id_raises():
    plan = _plan(
        [TRIGGER, {"id": "node-1", "templateId": "ai.notion_search", "role": "x", "description": "d"}],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_edge_with_missing_node_raises():
    plan = _plan([TRIGGER], [{"source": "node-1", "target": "node-99"}])
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


# --- MCP 의사 템플릿 게이팅 ---
def test_mcp_template_rejected_without_catalog():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "templateId": "ai.mcp", "role": "커스텀", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError) as excinfo:
        PlanValidator.validate(plan)
    assert "MCP" in str(excinfo.value)


def test_mcp_template_passes_with_catalog():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "templateId": "ai.mcp", "role": "커스텀", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    PlanValidator.validate(plan, allowed_mcp_catalog_ids={"cat-1"})
