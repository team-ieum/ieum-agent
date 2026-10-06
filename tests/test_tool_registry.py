import json

import pytest

from tools.registry import (
    tool_param_spec,
    required_user_params,
    service_blueprint,
    all_blueprints,
    brand_for_node,
    apply_service_brand,
    tool_form_schema,
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


# --- 도구 필드 스키마 (IEUM-AI-60) -------------------------------------------

def _schema_tool(name: str) -> dict:
    return next(t for t in tool_form_schema()["tools"] if t["name"] == name)


def test_tool_form_schema_sheets_append_fields():
    assert _schema_tool("builtin:google_sheets_append")["fields"] == [
        {"name": "spreadsheet_id", "title": "스프레드시트", "description": "대상 스프레드시트",
         "type": "string", "required": True, "optionsSource": "google.spreadsheets"},
        {"name": "cell_range", "title": "범위", "description": "예: A:C",
         "type": "string", "required": True},
        {"name": "values", "title": "값", "description": '2차원 배열 JSON (예: [["홍길동", 100]])',
         "type": "string", "required": True},
        {"name": "sheet_name", "title": "워크시트", "description": "대상 워크시트(탭)",
         "type": "string", "required": False,
         "optionsSource": "google.worksheets", "optionsInputs": ["spreadsheet_id"]},
    ]


def test_tool_form_schema_covers_tool_map_without_injected_params():
    from tools import _TOOL_MAP

    schema = tool_form_schema()
    assert [t["name"] for t in schema["tools"]] == list(_TOOL_MAP)
    for t in schema["tools"]:
        assert not {f["name"] for f in t["fields"]} & RUNTIME_INJECTED_PARAMS, t["name"]
    json.dumps(schema)  # 라우트 응답으로 직렬화 가능해야 한다


@pytest.mark.parametrize("tool, field, expected_type, required", [
    ("builtin:google_calendar_update", "summary", "string", False),   # Optional[str]
    ("builtin:workflow_context", "node_id", "string", False),         # str | None
    ("builtin:http_fetch", "headers_json", "string", False),          # str | dict | None → 첫 타입
    ("builtin:web_search", "max_results", "integer", False),
    ("builtin:text_extract", "find_all", "boolean", False),
])
def test_tool_form_schema_json_types(tool, field, expected_type, required):
    f = next(f for f in _schema_tool(tool)["fields"] if f["name"] == field)
    assert (f["type"], f["required"]) == (expected_type, required)


def test_tool_form_schema_field_without_meta_uses_param_name():
    f = next(f for f in _schema_tool("builtin:json_parse")["fields"]
             if f["name"] == "json_string")
    assert f == {"name": "json_string", "title": "json_string",
                 "type": "string", "required": True}


def test_tools_schema_route_returns_registry_schema():
    """LLM 자격증명 헤더 없이 열린다(정적 메타, 사용자 인증은 BE가 맡는다)."""
    from fastapi.testclient import TestClient
    from main import app

    resp = TestClient(app).get("/v1/tools/schema")
    assert resp.status_code == 200
    assert resp.json() == tool_form_schema()


def test_tool_form_schema_hides_agent_bound_context():
    """workflow_context_data는 agent가 실행 시 바인딩한다(agents/base.py _bind_workflow_context).
    폼에 필수 필드로 나가면 사용자가 내부 값을 넣어야 하는 것처럼 보인다."""
    names = {f["name"] for f in _schema_tool("builtin:workflow_context")["fields"]}
    assert names == {"action", "node_id", "field_path"}


def test_json_type_maps_dict_to_object():
    from tools.registry import _json_type

    assert _json_type(dict) == "object"


# --- 앱별 드롭다운 공급원 (IEUM-AI-62) ----------------------------------------

_EXPECTED_OPTION_FIELDS = {
    ("builtin:google_sheets_read", "spreadsheet_id"), ("builtin:google_sheets_read", "sheet_name"),
    ("builtin:google_sheets_write", "spreadsheet_id"), ("builtin:google_sheets_write", "sheet_name"),
    ("builtin:google_sheets_append", "spreadsheet_id"), ("builtin:google_sheets_append", "sheet_name"),
    ("builtin:google_calendar_create", "calendar_id"),
    ("builtin:google_calendar_list", "calendar_id"),
    ("builtin:google_calendar_update", "calendar_id"), ("builtin:google_calendar_update", "event_id"),
    ("builtin:google_drive_read", "file_id"),
    ("builtin:google_drive_upload", "folder_id"),
    ("builtin:notion_create_page", "parent_page_id"),
    ("builtin:notion_read_page", "page_id"),
    ("builtin:notion_update_page", "page_id"),
    ("builtin:notion_append_block", "page_id"),
    ("builtin:notion_query_database", "database_id"),
}


def test_tool_form_schema_공급원이_붙는_필드_집합():
    """도구 필드 덮어쓰기에 optionsSource가 붙은 (도구, 필드) 집합을 고정한다 — 의도치 않게 번지거나 빠지면 실패."""
    actual = {(t["name"], f["name"])
              for t in tool_form_schema()["tools"] for f in t["fields"] if "optionsSource" in f}
    assert actual == _EXPECTED_OPTION_FIELDS


@pytest.mark.parametrize("field, title, source, inputs", [
    ("calendar_id", "캘린더", "google.calendars", None),
    ("event_id", "일정", "google.events", ["calendar_id"]),
    ("file_id", "파일", "google.files", None),
    ("folder_id", "폴더", "google.folders", None),
    ("page_id", "페이지", "notion.pages", None),
    ("parent_page_id", "상위 페이지", "notion.pages", None),
    ("database_id", "데이터베이스", "notion.databases", None),
])
def test_tool_form_schema_app_option_sources(field, title, source, inputs):
    """공급원 키는 BE OptionSource.key()와 1:1 계약(IEUM-BE-72·73)."""
    f = next(f for t in tool_form_schema()["tools"] for f in t["fields"] if f["name"] == field)
    assert (f["title"], f["optionsSource"], f.get("optionsInputs")) == (title, source, inputs)
    assert f["description"]


def test_tool_form_schema_event_id는_뒤의_calendar_id를_가리킨다():
    """필드 순서는 시그니처 순서 그대로 — optionsInputs가 뒤 필드를 가리킬 수 있다(FE-51 명세)."""
    names = [f["name"] for f in _schema_tool("builtin:google_calendar_update")["fields"]]
    assert names.index("event_id") < names.index("calendar_id")
