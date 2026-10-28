import pytest

from tools.registry import (
    brand_for_entry,
    resolve_action_fn,
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
    for svc in ("notion", "slack", "google_sheets", "gmail", "web_search", "github"):
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


# --- ACTION 실행용 tool_key 해석 (노드 카탈로그 ②) ---------------------------------------------

def test_resolve_action_fn_native_and_github():
    from tools import _TOOL_MAP
    from tools.github import github_create_issue, github_list_issues

    assert resolve_action_fn("slack") is _TOOL_MAP["slack"]
    assert resolve_action_fn("builtin:notion_search") is _TOOL_MAP["builtin:notion_search"]
    assert resolve_action_fn("builtin:github_list_issues") is github_list_issues
    assert resolve_action_fn("builtin:github_create_issue") is github_create_issue


def test_calendar_delete는_ACTION_전용이다():
    """AI 노드 tools에 실리면 LLM이 일정을 지울 수 있다(spec §4.3) — _TOOL_MAP 밖, resolve_action_fn만 푼다."""
    from tools import _TOOL_MAP, get_tools_for_request
    from tools.google_calendar import google_calendar_delete

    assert resolve_action_fn("builtin:google_calendar_delete") is google_calendar_delete
    assert "builtin:google_calendar_delete" not in _TOOL_MAP
    assert get_tools_for_request([{"name": "builtin:google_calendar_delete"}]) == []


@pytest.mark.parametrize("key", [
    "mcp", "github_list_issues", "builtin:github_nope", "builtin:github_", "builtin:github_list_issues ",
    "builtin:does_not_exist", "", None, 5, ["builtin:github_list_issues"],
])
def test_resolve_action_fn_unknown_returns_none(key):
    assert resolve_action_fn(key) is None


def test_github_create_issue_stays_out_of_tool_map():
    """github 쓰기 도구가 _TOOL_MAP에 들어가면 AI 노드 tools: [] 계약(tools_must_be_empty)이 깨진다."""
    from tools import _TOOL_MAP, get_tools_for_request
    from tools.github import github_create_issue

    assert github_create_issue not in _TOOL_MAP.values()
    assert get_tools_for_request([{"name": "builtin:github_create_issue"}]) == []


def test_github_create_issue_in_dynamic_blueprint():
    action = service_blueprint("github")["actions"]["github_create_issue"]
    assert action["token"]["class"] == "runtime_injected"
    assert action["title"]["class"] == "required"
    assert action["body"]["class"] == "optional"


@pytest.mark.parametrize("event", ["github.list_issues", "github.create_issue"])
def test_brand_for_github_action(event):
    """ACTION은 event 앱의 brand다 — 기본값 webhook으로 떨어지면 BE 연동 목록(config.brand)에서 빠진다."""
    assert brand_for_node({"type": "ACTION", "event": event, "config": {}}) == "github"


def test_brand_for_entry_ai_without_tool_uses_service_field():
    """로더 안에서는 intent 경로를 못 탄다 — tool_key 없는 AI 항목은 service 필드로 brand를 정한다."""
    base = {"node_type": "AI", "tool_key": None, "fixed": {"type": "AI", "config": {"tools": []}}}
    assert brand_for_entry({**base, "service": "github"}) == "github"
    assert brand_for_entry(base) == "openai"
    assert brand_for_entry({**base, "service": "no-such-service"}) == "openai"


def test_brand_for_entry_tool_preset_without_app():
    """앱 없는 도구 프리셋(ai.web_search·ai.http_fetch)은 fixed로 brand_for_node가 답한다.
    이벤트·앱 프리셋 brand는 apps.json에서 온다(test_catalog_loader.test_brand_from_apps_json)."""
    for tool in ("builtin:web_search", "builtin:http_fetch"):
        entry = {"node_type": "AI", "tool_key": tool, "fixed": {"type": "AI", "config": {"tools": [{"name": tool}]}}}
        assert brand_for_entry(entry) == "openai", tool


@pytest.mark.parametrize("event,brand", [
    ("gmail.new_email", "gmail"), ("github.new_issue", "github"), ("schedule.every", "webhook"),
])
def test_trigger_brand_from_event(event, brand):
    assert brand_for_node({"type": "TRIGGER", "event": event, "config": {}}) == brand


def test_brand_for_node_reads_event_not_legacy_fields():
    """AI가 아닌 노드는 event 앱 brand만 본다 — config.triggerType·ACTION config.tools는 읽지 않는다(spec §4)."""
    assert brand_for_node({"type": "ACTION", "event": "google_sheets.read", "config": {}}) == "sheets"
    # agent가 모르는 event(BE가 먼저 올린 새 이벤트)는 node_type 기본값 — 옛 필드로 추정하지 않는다
    assert brand_for_node({"type": "TRIGGER", "event": "nope.x",
                           "config": {"triggerType": "GITHUB_NEW_ISSUE"}}) == "webhook"
    assert brand_for_node({"type": "ACTION", "event": "nope.x",
                           "config": {"tools": [{"name": "slack"}]}}) == "webhook"
    # AI는 event(늘 ai.agent·ai.reasoning)가 아니라 도구로 정한다
    assert brand_for_node({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "slack"}]}}) == "slack"
    assert brand_for_node({"type": "AI", "event": "ai.agent", "config": {"tools": []}}) == "openai"


def _calendar_update_tool(config=None):
    from tools import get_tools_for_request
    item = {"name": "builtin:google_calendar_update"}
    if config is not None:
        item["config"] = config
    [tool] = get_tools_for_request([item])
    return tool.func


_RISKY = ("scope", "send_updates", "attendees")


def test_AI_캘린더_수정은_위험_인자를_숨기고_안전한_기본값으로_고정한다():
    import inspect
    fn = _calendar_update_tool()
    params = inspect.signature(fn).parameters
    for k in _RISKY:
        assert k not in params
        assert k not in fn.__annotations__
    assert fn.keywords == {"scope": "this", "send_updates": "", "attendees": None}


def test_AI_캘린더_수정은_노드_설정값이_기본값을_이긴다():
    import inspect
    fn = _calendar_update_tool({"scope": "all"})
    assert fn.keywords["scope"] == "all"
    assert fn.keywords["send_updates"] == ""
    assert fn.keywords["attendees"] is None
    params = inspect.signature(fn).parameters
    for k in _RISKY:
        assert k not in params


def test_ACTION_캘린더_수정은_위험_인자를_그대로_연다():
    import inspect
    params = inspect.signature(resolve_action_fn("builtin:google_calendar_update")).parameters
    for k in _RISKY:
        assert k in params


def test_slack_봇_도구는_token을_주입받고_brand는_slack이다():
    spec = tool_param_spec("builtin:slack_send_channel_message")
    assert spec["token"]["class"] == "runtime_injected"
    assert [n for n, s in spec.items() if s["class"] == "required"] == ["channel", "text"]
    assert brand_for_node({"type": "AI", "event": "ai.agent",
                           "config": {"tools": [{"name": "builtin:slack_send_channel_message"}]}}) == "slack"


def test_discord_봇_도구는_token과_허용_서버를_주입받고_brand는_discord다():
    spec = tool_param_spec("builtin:discord_send_channel_message")
    assert (spec["token"]["class"], spec["allowed_guild_ids"]["class"]) == ("runtime_injected", "runtime_injected")
    assert [n for n, s in spec.items() if s["class"] == "required"] == ["channel_id", "content"]
    assert brand_for_node({"type": "AI", "event": "ai.agent",
                           "config": {"tools": [{"name": "builtin:discord_send_channel_message"}]}}) == "discord"


def test_notion_DB_행_도구는_token을_주입받고_brand는_notion이다():
    for key, required in (("builtin:notion_create_row", ["data_source_id"]), ("builtin:notion_update_row", ["row_id"])):
        spec = tool_param_spec(key)
        assert spec["token"]["class"] == "runtime_injected"
        assert [n for n, s in spec.items() if s["class"] == "required"] == required
        assert brand_for_node({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": key}]}}) == "notion"
