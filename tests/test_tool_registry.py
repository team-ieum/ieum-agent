from tools.registry import (
    tool_param_spec,
    required_user_params,
    service_blueprint,
    all_blueprints,
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
