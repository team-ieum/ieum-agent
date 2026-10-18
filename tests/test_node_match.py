"""노드 → 카탈로그 항목 판정(IEUM-AI-78, spec §4). event가 유일한 판정이고, AI 노드만 프리셋 match로 칸 규칙을 고른다."""
import copy

import pytest

from core import template_registry as tr


@pytest.mark.parametrize("node, expected", [
    ({"type": "AI", "event": "ai.agent", "config": {"agentType": "react", "tools": [{"name": "discord"}]}}, "ai.agent"),
    ({"type": "AI", "event": "ai.reasoning", "config": {"agentType": "simple", "tools": []}}, "ai.reasoning"),
    ({"type": "AI", "event": "ai.reasoning", "config": {"agentType": "simple"}}, "ai.reasoning"),
    # 도구 없는 react AI = GitHub 서브에이전트 폼(프리셋 match)
    ({"type": "AI", "event": "ai.agent", "config": {"agentType": "react", "tools": []}}, "ai.github_query"),
    ({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "mcp", "config": {"catalogId": "c"}}]}},
     "ai.mcp"),
    ({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "builtin:web_search"}]}}, "ai.web_search"),
    # 노드 type 표기는 보지 않는다 — 프리셋 match의 type은 event가 정한 AI(정적 검증 경로는 WorkflowNode를 거치지 않는다)
    ({"type": "ai", "event": "ai.agent", "config": {"tools": [{"name": "mcp", "config": {}}]}}, "ai.mcp"),
    ({"event": "ai.agent", "config": {"tools": [{"name": "builtin:web_search"}]}}, "ai.web_search"),
    # 도구 2개 이상이면 첫 도구 전용 폼이 아니라 AI 에이전트 폼으로 연다
    ({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "builtin:web_search"}, {"name": "discord"}]}},
     "ai.agent"),
    ({"type": "TRIGGER", "event": "schedule.every", "config": {"cron": "0 9 * * *"}}, "schedule.every"),
    ({"type": "TRIGGER", "event": "gmail.new_email", "config": {"query": ""}}, "gmail.new_email"),
    ({"type": "TRIGGER", "event": "github.new_issue", "config": {"repoId": "1"}}, "github.new_issue"),
    ({"type": "TRIGGER", "event": "manual.run", "config": {}}, "manual.run"),
    ({"type": "HTTP", "event": "http.request", "config": {"url": "x"}}, "http.request"),
    ({"type": "ACTION", "event": "slack.send_message", "config": {"message": "안녕"}}, "slack.send_message"),
])
def test_event가_항목을_정한다(node, expected):
    assert tr.match_entry(node)["id"] == expected


@pytest.mark.parametrize("node, expected", [
    # 옛 필드는 읽지 않는다 — event가 이긴다
    ({"type": "TRIGGER", "event": "manual.run", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},
     "manual.run"),
    ({"type": "ACTION", "event": "slack.send_message", "config": {"tools": [{"name": "discord"}]}},
     "slack.send_message"),
    # type이 event와 달라도 event가 판정한다(type은 Task 4 스키마가 event로 덮는다)
    ({"type": "TRIGGER", "event": "slack.send_message", "config": {}}, "slack.send_message"),
    # 프리셋 match는 AI 항목에만 — AI가 아닌 event는 tools 모양이 프리셋과 맞아도 그 event 항목이다
    ({"type": "AI", "event": "http.request", "config": {"tools": [{"name": "builtin:web_search"}]}}, "http.request"),
])
def test_event가_옛_필드와_type을_이긴다(node, expected):
    assert tr.match_entry(node)["id"] == expected


@pytest.mark.parametrize("node", [
    {"type": "TRIGGER", "config": {"triggerType": "MANUAL"}},                       # event 없음(옛 노드)
    {"type": "ACTION", "config": {"tools": [{"name": "slack"}]}},
    {"type": "HTTP", "config": {"url": "x"}},
    {"type": "TRIGGER", "event": "stripe.new_payment", "config": {}},              # agent가 모르는 event
    {"type": "AI", "event": "ai.notion_create_page", "config": {"tools": []}},      # 프리셋 id는 event가 아니다
    {"type": "TRIGGER", "event": None, "config": {"triggerType": "MANUAL"}},
    {"type": "TRIGGER", "event": ["manual.run"], "config": {}},                    # 문자열 아님(해시 불가)
    {"type": "LOOP", "config": {}},
])
def test_event가_없거나_모르면_None(node):
    assert tr.match_entry(node) is None


def test_match_entry는_옛_저장_모양을_정규화하지_않는다():
    """문자열 tools 원소(옛 저장 모양) 정규화는 지웠다 — 프리셋 match가 안 맞으면 event 항목이다. 노드 원본은 바꾸지 않는다."""
    node = {"type": "ai", "event": "ai.agent", "config": {"tools": ["builtin:web_search"]}}
    before = copy.deepcopy(node)
    assert tr.match_entry(node)["id"] == "ai.agent"
    assert node == before
    assert tr.match_entry("nope") is None


def test_builder_entry_fixed_matches_itself():
    """각 builder 항목의 fixed(로더가 event를 넣은 노드 모양)는 그 항목으로 열린다.
    ai.agent만 예외 — fixed(tools [], react)는 도구 없는 react라 ai.github_query 폼이다. 도구 하나를 넣으면 ai.agent."""
    for t in tr.builder_entries():
        expected = "ai.github_query" if t["id"] == "ai.agent" else t["id"]
        assert tr.match_entry(t["fixed"])["id"] == expected, t["id"]
    fixed = tr.get_template("ai.agent")["fixed"]
    node = {**fixed, "config": {**fixed["config"], "tools": [{"name": "discord"}]}}
    assert tr.match_entry(node)["id"] == "ai.agent"


def test_프리셋_match_후보는_match를_쓴_프리셋만_구체적인_것부터():
    expected = [t for t in tr.all_entries() if t.get("preset") and t.get("match")]
    got = tr._preset_matchers()
    assert expected and sorted(t["id"] for t in got) == sorted(t["id"] for t in expected)
    assert [(-len(t["match"]), t["id"]) for t in got] == sorted((-len(t["match"]), t["id"]) for t in got)
    assert all(t["node_type"] == "AI" for t in got)


def test_레거시_판정_함수와_별칭은_없다():
    for name in ("event_identity", "_legacy_identified", "_match_entries", "_default_match", "_aliases"):
        assert not hasattr(tr, name), name


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


def test_agent_tool_choices_exclude_dynamic_mount_services():
    """AI 노드 GitHub 계약은 tools: [] — 선택지에 github 액션이 있으면 FE가 tools에 넣어 계약이 깨진다."""
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert not [c for c in tools["choices"] if "github" in c["id"]]
    assert tr.get_template("github.list_issues")["builder"] is True   # 카탈로그엔 따로 있다


# ── resolve_template_for_node: 생성 템플릿(dehydrate·허용 키) ──

@pytest.mark.parametrize("node, expected", [
    ({"type": "TRIGGER", "event": "schedule.every", "config": {"cron": "0 9 * * *"}}, "schedule.every"),
    # event가 옛 triggerType을 이긴다
    ({"type": "TRIGGER", "event": "schedule.every", "config": {"triggerType": "MANUAL"}}, "schedule.every"),
    ({"type": "TRANSFORM", "event": "transform.map", "config": {"mappings": {}}}, "transform.map"),
    ({"type": "APPROVAL", "event": "approval.request", "config": {}}, "approval.request"),
    # AI는 tools로 생성 프리셋을 고른다(프리셋 노드의 event는 ai.agent, generation:false)
    ({"type": "AI", "event": "ai.agent", "config": {"agentType": "react",
                                                    "tools": [{"name": "builtin:notion_create_page"}]}},
     "ai.notion_create_page"),
    ({"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "mcp", "config": {}}]}}, "ai.mcp"),
    ({"type": "AI", "event": "ai.reasoning", "config": {"agentType": "simple", "tools": []}}, "ai.reasoning"),
    ({"type": "AI", "event": "ai.agent", "config": {"agentType": "react", "tools": []}}, "ai.github_query"),
])
def test_resolve_template_for_node(node, expected):
    assert tr.resolve_template_for_node(node)["id"] == expected


@pytest.mark.parametrize("node", [
    {"type": "TRIGGER", "event": "gmail.new_email", "config": {}},       # 빌더 전용(generation:false) → pass-through
    {"type": "ACTION", "event": "slack.send_message", "config": {}},
    {"type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}},  # event 없음
    {"type": "TRANSFORM", "config": {"mappings": {}}},
    {"type": "AI", "config": {"tools": [{"name": "builtin:notion_create_page"}]}},
    {"type": "TRIGGER", "event": "stripe.new_payment", "config": {}},    # 모르는 event
])
def test_resolve_template_for_node_은_생성_템플릿이_없으면_None(node):
    assert tr.resolve_template_for_node(node) is None


def test_preset_node_with_ai_agent_event_keeps_preset_match_and_passes_static_check():
    # event로 ai.agent 항목을 고르면 tools 필수 칸에 걸려 실행이 400이 된다 — 프리셋 match가 ai.github_query를 고른다.
    from core.validators.static_validator import field_errors
    node = {"id": "a", "type": "AI", "event": "ai.agent",
            "config": {"llmProvider": "GEMINI", "model": "m", "prompt": "p", "agentType": "react", "tools": []}}
    assert tr.match_entry(node)["id"] == "ai.github_query"
    assert field_errors(node) == []
