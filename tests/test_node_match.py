import copy

import pytest

from core import template_registry as tr
from core.template_registry import TemplateSchemaError
from tests.catalog_paths import copy_catalog, edit_entry


@pytest.mark.parametrize("node, expected", [
    ({"type": "AI", "config": {"agentType": "react", "tools": [{"name": "discord"}]}}, "ai.agent"),
    ({"type": "AI", "config": {"agentType": "simple", "tools": []}}, "ai.reasoning"),
    ({"type": "AI", "config": {"agentType": "simple"}}, "ai.reasoning"),
    # serviceType 없는 옛 GitHub 노드(AI-55 이전 저장분)도 GitHub 폼으로 연다
    ({"type": "AI", "config": {"agentType": "react", "tools": []}}, "ai.github_query"),
    ({"type": "AI", "config": {"tools": [{"name": "mcp", "config": {"catalogId": "c"}}]}}, "ai.mcp"),
    ({"type": "AI", "config": {"tools": [{"name": "builtin:web_search"}]}}, "ai.web_search"),
    # 도구 2개 이상이면 첫 도구 전용 폼이 아니라 AI 에이전트 폼으로 연다
    ({"type": "AI", "config": {"tools": [{"name": "builtin:web_search"}, {"name": "discord"}]}}, "ai.agent"),
    ({"type": "AI", "config": {"tools": [{"name": "mcp", "config": {"catalogId": "c"}}, {"name": "discord"}]}},
     "ai.agent"),
    ({"type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}}, "schedule.every"),
    ({"type": "TRIGGER", "config": {"triggerType": "GMAIL_NEW_EMAIL", "query": ""}}, "gmail.new_email"),
    ({"type": "TRIGGER", "config": {"triggerType": "GITHUB_NEW_ISSUE", "repoId": "1"}}, "github.new_issue"),
    # serviceType이 빠진 저장분도 triggerType만으로 연다(match는 serviceType을 보지 않는다)
    ({"type": "trigger", "config": {"triggerType": "GITHUB_NEW_ISSUE"}}, "github.new_issue"),
    ({"type": "TRIGGER", "config": {}}, "manual.run"),
    ({"type": "TRIGGER", "config": {"triggerType": "MANUAL"}}, "manual.run"),
    ({"type": "HTTP", "config": {"url": "x"}}, "http.request"),
    ({"type": "ACTION", "config": {"tools": [{"name": "slack", "config": {}}]}}, "slack.send_message"),
])
def test_match_saved_nodes(node, expected):
    assert tr.match_entry(node)["id"] == expected


@pytest.mark.parametrize("node, expected", [
    ({"type": "ai", "config": {"tools": ["builtin:web_search"]}}, "ai.web_search"),
    ({"type": "AI", "config": {"tools": ["discord"]}}, "ai.agent"),
])
def test_match_normalizes_legacy_node_shape(node, expected):
    """옛 저장 모양(소문자 type, 문자열 tools — BE AgentNodeExecutor.parseTools가 실행함)도 매칭하고 원본은 그대로 둔다."""
    before = copy.deepcopy(node)
    assert tr.match_entry(node)["id"] == expected
    assert node == before


def test_builder_entry_fixed_matches_itself():
    """각 builder 항목의 fixed(노드 모양 그대로)는 그 항목으로 열린다.
    ai.agent만 예외 — 필수 tools가 빈 fixed는 완성 노드가 아니라서, 도구 하나를 넣은 노드로 확인한다."""
    for t in tr.builder_entries():
        if t["id"] != "ai.agent":
            assert tr.match_entry(t["fixed"])["id"] == t["id"]
    fixed = tr.get_template("ai.agent")["fixed"]
    node = {**fixed, "config": {**fixed["config"], "tools": [{"name": "discord"}]}}
    assert tr.match_entry(node)["id"] == "ai.agent"


def test_match_unknown_node_type_is_none():
    assert tr.match_entry({"type": "LOOP", "config": {}}) is None


def test_match_and_builder_entries_order_most_specific_first():
    # 매칭 후보(builder + match 보유 프리셋)의 평가 순서. 프리셋은 빌더 목록엔 없다(설계 결정 4).
    ids = [t["id"] for t in tr._match_entries()]
    assert ids.index("ai.github_query") < ids.index("ai.reasoning") < ids.index("ai.agent")
    # 카탈로그 API(builder_entries)도 같은 match 평가 순서다(id 정렬이면 ai.agent가 먼저 온다)
    b = [t["id"] for t in tr.builder_entries()]
    assert b.index("ai.reasoning") < b.index("ai.agent")
    assert all(t["builder"] for t in tr.builder_entries())


def test_agent_tool_choices_cover_actions():
    from tools import _TOOL_MAP
    agent = tr.get_template("ai.agent")
    tools = next(f for f in agent["inputFields"] if f["key"] == "tools")
    # github 액션은 _TOOL_MAP 밖(동적 서브에이전트 서비스)이라 AI 노드 도구 선택지가 아니다
    actions = {t["tool_key"]: t["title"] for t in tr.all_entries()
               if t["node_type"] == "ACTION" and t["builder"] and t["tool_key"] in _TOOL_MAP}
    assert {c["id"]: c["name"] for c in tools["choices"]} == actions
    assert tools["required"] is True and tools["list"] is True
    assert agent["generation"] is False


@pytest.mark.parametrize("name, match", [
    # branch.if는 match 키가 없다 — 로더 _default_match가 {type: CONDITION}을 만든다.
    ("http.request", {"type": "CONDITION"}),
    # match 보유 프리셋끼리도 매칭 후보라 겹치면 거부한다(ai.http_fetch의 match 그대로)
    ("ai.web_search", {"type": "AI", "config.tools.0.name": "builtin:http_fetch", "config.tools.1": None}),
])
def test_duplicate_matchable_match_rejected(tmp_path, monkeypatch, name, match):
    root = copy_catalog(tmp_path, monkeypatch)
    edit_entry(root, name, lambda raw: raw.__setitem__("match", match))
    with pytest.raises(TemplateSchemaError, match="match"):
        tr.load_templates(force=True)


def test_agent_tool_choices_exclude_dynamic_mount_services():
    """AI 노드 GitHub 계약은 tools: [] — 선택지에 github 액션이 있으면 FE가 tools에 넣어 계약이 깨진다."""
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert not [c for c in tools["choices"] if "github" in c["id"]]
    assert tr.get_template("github.list_issues")["builder"] is True   # 카탈로그엔 따로 있다


def test_event_only_app_trigger_does_not_fall_into_manual_catch_all():
    from core.template_registry import match_entry
    node = {"type": "TRIGGER", "event": "gmail.new_email", "config": {"query": "is:unread"}}
    assert match_entry(node)["id"] == "gmail.new_email"


def test_unknown_event_falls_back_to_match_rules():
    from core.template_registry import match_entry
    node = {"type": "TRIGGER", "event": "nope.x", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}}
    assert match_entry(node)["id"] == "schedule.every"


def test_event_only_schedule_resolves_generation_template():
    from core.template_registry import resolve_template_for_node
    node = {"type": "TRIGGER", "event": "schedule.every", "config": {"cron": "0 9 * * *"}}
    assert resolve_template_for_node(node)["id"] == "schedule.every"


def test_event_only_builder_only_event_stays_unresolved():
    # 앱 트리거·액션은 generation:false — 지금처럼 pass-through 대상이다.
    from core.template_registry import resolve_template_for_node
    assert resolve_template_for_node({"type": "TRIGGER", "event": "gmail.new_email", "config": {}}) is None


def test_preset_node_with_ai_agent_event_still_resolves_by_tools():
    from core.template_registry import resolve_template_for_node
    node = {"type": "AI", "event": "ai.agent",
            "config": {"agentType": "react", "tools": [{"name": "builtin:notion_create_page"}]}}
    assert resolve_template_for_node(node)["id"] == "ai.notion_create_page"


def test_preset_node_with_ai_agent_event_keeps_preset_match_and_passes_static_check():
    # event로 ai.agent 항목을 고르면 tools 필수 칸에 걸려 실행이 400이 된다(Review Focus 2).
    from core.template_registry import match_entry
    from core.validators.static_validator import field_errors
    node = {"id": "a", "type": "AI", "event": "ai.agent",
            "config": {"llmProvider": "GEMINI", "model": "m", "prompt": "p", "agentType": "react", "tools": []}}
    assert match_entry(node)["id"] == "ai.github_query"
    assert field_errors(node) == []


def test_stale_event_loses_to_legacy_fields():
    # 빌더로 MANUAL로 바꿨는데 BE 병합이 옛 event를 이어받은 노드(Review Focus 1)
    from core.template_registry import match_entry, resolve_template_for_node
    node = {"type": "TRIGGER", "event": "schedule.every", "config": {"triggerType": "MANUAL"}}
    assert match_entry(node)["id"] == "manual.run"
    assert resolve_template_for_node(node)["id"] == "manual.run"
