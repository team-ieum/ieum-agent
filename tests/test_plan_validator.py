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
        ],
        [
            {"source": "node-1", "target": "node-2"},
            {"source": "node-2", "target": "node-3"},
        ],
    )
    PlanValidator.validate(plan)


def test_plan_mcp_tool_rejected_at_generation():
    # T4: 카탈로그가 없으면 Plan에서 'mcp' 도구 배정은 차단되어야 한다 (환각 방지)
    node = {"id": "node-2", "type": "AI", "role": "커스텀", "description": "d", "tools": ["mcp"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    with pytest.raises(PlanValidationError) as excinfo:
        PlanValidator.validate(plan)
    assert "mcp" in str(excinfo.value).lower() or "MCP" in str(excinfo.value)


def test_plan_mcp_tool_with_valid_catalog_id_passes():
    # 생성 주입: 'mcp:<catalogId>'의 catalogId가 허용 집합에 있으면 통과 + 형식 유지
    node = {"id": "node-2", "type": "AI", "role": "커스텀", "description": "d", "tools": ["mcp:cat-1"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    PlanValidator.validate(plan, allowed_mcp_catalog_ids={"cat-1"})
    assert plan.nodes[1].tools == ["mcp:cat-1"]


def test_plan_mcp_tool_with_unknown_catalog_id_rejected():
    # 허용 집합에 없는 catalogId는 차단(환각 catalogId 방지)
    node = {"id": "node-2", "type": "AI", "role": "커스텀", "description": "d", "tools": ["mcp:cat-x"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    with pytest.raises(PlanValidationError):
        PlanValidator.validate(plan, allowed_mcp_catalog_ids={"cat-1"})


# --- T3(b): 서비스→도구 결정론 매핑 (이름 환각 차단) ---
def test_plan_missing_prefix_tool_is_canonicalized():
    """프리픽스 누락 맨이름은 차단이 아니라 정확한 builtin: 키로 자동 교정된다 (in-place)."""
    node = {"id": "node-2", "type": "AI", "role": "노션", "description": "d", "tools": ["notion_create_page"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    PlanValidator.validate(plan)
    # plan.nodes[1].tools가 정확 키로 교정되어야 함
    assert plan.nodes[1].tools == ["builtin:notion_create_page"]


def test_plan_alias_tool_is_canonicalized():
    """흔한 별칭('search')도 정확한 도구 키로 환원된다."""
    node = {"id": "node-2", "type": "AI", "role": "검색", "description": "d", "tools": ["search", "fetch"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    PlanValidator.validate(plan)
    assert plan.nodes[1].tools == ["builtin:web_search", "builtin:http_fetch"]


def test_plan_subagent_tool_is_stripped():
    """서브 에이전트(github/transform/web 등)는 실행 시 처리되므로 검증 단계에서 제거된다(차단 아님)."""
    node = {"id": "node-2", "type": "AI", "role": "깃헙", "description": "d",
            "tools": ["github_list_pull_requests", "builtin:github_list_pull_requests", "GitHub_List_Issues", "transform_agent", "web_agent", "slack"]}
    plan = _plan([TRIGGER, node], [{"source": "node-1", "target": "node-2"}])
    PlanValidator.validate(plan)
    assert plan.nodes[1].tools == ["slack"]


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
