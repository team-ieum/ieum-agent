import pytest
from api.schemas.generate_workflow import WorkflowPlanSchema
from core.validators.plan_validator import PlanValidator, PlanValidationError


def _plan(nodes, edges=None):
    return WorkflowPlanSchema(nodes=nodes, edges=edges or [], justification="테스트 근거")


TRIGGER = {"id": "node-1", "type": "TRIGGER", "role": "트리거", "description": "스케줄"}


# --- 기본 구조 검증 ---
def test_valid_plan_passes():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "type": "AI", "role": "뉴스 검색", "description": "d", "tools": ["builtin:web_search"]}],
        [{"source": "node-1", "target": "node-2"}],
    )
    PlanValidator.validate(plan)


def test_missing_trigger_raises():
    plan = _plan([{"id": "node-2", "type": "AI", "role": "x", "description": "d"}])
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_first_node_must_be_trigger():
    plan = _plan(
        [{"id": "node-2", "type": "AI", "role": "x", "description": "d"}, TRIGGER],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


def test_http_node_with_prohibited_service_raises():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "type": "HTTP", "role": "Notion 페이지 저장", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan)


# --- T3: AI 노드 도구 이름 검증 ---
def test_plan_valid_tool_names_pass():
    plan = _plan(
        [
            TRIGGER,
            {"id": "node-2", "type": "AI", "role": "검색", "description": "d", "tools": ["builtin:web_search"]},
            {"id": "node-3", "type": "AI", "role": "알림", "description": "d", "tools": ["slack"]},
            {"id": "node-4", "type": "AI", "role": "커스텀", "description": "d", "tools": ["mcp"]},
        ],
        [
            {"source": "node-1", "target": "node-2"},
            {"source": "node-2", "target": "node-3"},
            {"source": "node-3", "target": "node-4"},
        ],
    )
    PlanValidator.validate(plan)


def test_plan_missing_prefix_tool_raises_with_hint():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "type": "AI", "role": "노션", "description": "d", "tools": ["notion_create_page"]}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError) as excinfo:
        PlanValidator.validate(plan)
    assert "builtin:notion_create_page" in str(excinfo.value)


def test_plan_unknown_tool_raises():
    plan = _plan(
        [TRIGGER, {"id": "node-2", "type": "AI", "role": "오타", "description": "d", "tools": ["builtin:notion_make_page"]}],
        [{"source": "node-1", "target": "node-2"}],
    )
    with pytest.raises(PlanValidationError) as excinfo:
        PlanValidator.validate(plan)
    assert "유효하지 않습니다" in str(excinfo.value)


def test_plan_ai_node_without_tools_pass():
    # tools 미지정(기본 []) AI 노드는 통과해야 함
    plan = _plan(
        [TRIGGER, {"id": "node-2", "type": "AI", "role": "단순 추론", "description": "d"}],
        [{"source": "node-1", "target": "node-2"}],
    )
    PlanValidator.validate(plan)
