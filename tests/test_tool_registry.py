from tools.registry import (
    tool_param_spec,
    required_user_params,
    service_blueprint,
    all_blueprints,
    brand_for_node,
    apply_service_brand,
    RUNTIME_INJECTED_PARAMS,
)


def test_param_spec_classifies_runtime_injected():
    spec = tool_param_spec("builtin:notion_create_page")
    assert spec["token"]["class"] == "runtime_injected"
    assert spec["parent_page_id"]["class"] == "required"


def test_required_excludes_runtime_and_optional():
    req = required_user_params("builtin:notion_create_page")
    assert "parent_page_id" in req
    assert "title" in req
    assert "token" not in req  # 런타임 주입 제외


def test_optional_param_detected():
    spec = tool_param_spec("builtin:notion_search")
    assert spec["filter_type"]["class"] == "optional"
    assert spec["filter_type"]["default"] == "page"


def test_google_access_token_is_runtime_injected():
    req = required_user_params("builtin:google_sheets_write")
    assert "access_token" not in req
    assert "spreadsheet_id" in req


def test_slack_webhook_runtime_injected():
    spec = tool_param_spec("slack")
    assert spec["webhook_url"]["class"] == "runtime_injected"
    assert spec["message"]["class"] == "required"


def test_service_blueprint_groups_tools_and_policy():
    bp = service_blueprint("notion")
    assert bp["service"] == "notion"
    assert "builtin:notion_create_page" in bp["tools"]
    assert "builtin:notion_search" in bp["tools"]
    assert "prompt_hint" in bp  # SERVICE_POLICY 합성 확인


def test_all_blueprints_covers_registered_services():
    bps = all_blueprints()
    for svc in ("notion", "slack", "google_sheets", "gmail", "web_search"):
        assert svc in bps


def test_github_policy_only_blueprint():
    bp = service_blueprint("github")
    # 노드 tools 계약: github는 tools를 비워야 하므로 tool-based 키가 없어야 한다
    assert bp["tools"] == {}
    assert bp["node_contract"] == "tools_must_be_empty"
    assert bp["mount"] == "dynamic_subagent"
    # 액션 스펙은 동적 마운트 함수에서 파생된다
    assert "github_list_pull_requests" in bp["actions"]
    pr = bp["actions"]["github_list_pull_requests"]
    assert pr["owner"]["class"] == "required"
    assert pr["token"]["class"] == "runtime_injected"


def test_github_included_in_all_blueprints():
    assert "github" in all_blueprints()


def test_runtime_injected_set_contract():
    # 백엔드 주입 자격증명이 검증/폼 노출에서 제외되는지 계약 고정
    for p in ("token", "access_token", "webhook_url"):
        assert p in RUNTIME_INJECTED_PARAMS


def test_brand_from_tool_key():
    node = {"type": "AI", "config": {"tools": [{"name": "builtin:notion_search"}]}}
    assert brand_for_node(node) == "notion"


def test_brand_key_normalized_for_google_services():
    sheets = {"type": "AI", "config": {"tools": [{"name": "builtin:google_sheets_write"}]}}
    drive = {"type": "AI", "config": {"tools": [{"name": "builtin:google_drive_read"}]}}
    cal = {"type": "AI", "config": {"tools": [{"name": "builtin:google_calendar_create"}]}}
    assert brand_for_node(sheets) == "sheets"
    assert brand_for_node(drive) == "google"
    assert brand_for_node(cal) == "google"


def test_brand_falls_back_to_node_type():
    # tool_key 없는 구조/트리거 노드는 node_type 기본 brand로 폴백
    assert brand_for_node({"type": "TRIGGER", "config": {}}) == "webhook"
    assert brand_for_node({"type": "CONDITION", "config": {}}) == "filter"
    # 도구 없는 AI 노드(동적 서브에이전트/순수 추론)는 AI 기본 brand
    assert brand_for_node({"type": "AI", "config": {"tools": []}}) == "openai"


def test_brand_ignores_mcp_and_unmapped_tools():
    # mcp 동적 도구는 서비스 식별 불가 → node_type 폴백
    node = {"type": "AI", "config": {"tools": [{"name": "mcp"}]}}
    assert brand_for_node(node) == "openai"


def test_apply_service_brand_injects_in_place():
    nodes = [
        {"type": "TRIGGER", "config": {}},
        {"type": "AI", "config": {"tools": [{"name": "slack"}]}},
        {"type": "AI"},  # config 없음 → 생성 후 주입
    ]
    apply_service_brand(nodes)
    assert nodes[0]["config"]["brand"] == "webhook"
    assert nodes[1]["config"]["brand"] == "slack"
    assert nodes[2]["config"]["brand"] == "openai"


def test_brand_field_in_universal_config_fields():
    # 주입한 brand가 검증 화이트리스트를 통과하도록 universal에 포함되어야 한다
    from core.template_registry import UNIVERSAL_CONFIG_FIELDS
    assert "brand" in UNIVERSAL_CONFIG_FIELDS


def test_github_subagent_node_brand_from_intent():
    # tool_key 없는 github 노드: 라벨/프롬프트 intent 태그 매칭으로 github brand 도출
    node = {
        "type": "AI",
        "label": "GitHub PR 목록 조회",
        "config": {"tools": [], "prompt": "team-ieum/ieum-backend의 PR 목록을 조회해줘"},
    }
    assert brand_for_node(node) == "github"


def test_toolless_ai_without_github_intent_falls_back():
    # github 의도가 없는 도구 없는 AI 노드는 서브에이전트로 오분류되지 않고 AI 기본 brand
    node = {
        "type": "AI",
        "label": "요약",
        "config": {"tools": [], "prompt": "이전 결과를 세 문장으로 요약해줘"},
    }
    assert brand_for_node(node) == "openai"
