import copy
import glob
import json
import os
import shutil

import pytest

from core import template_registry as tr
from core.template_registry import TemplateSchemaError


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
    ({"type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}}, "trigger.schedule"),
    ({"type": "TRIGGER", "config": {"triggerType": "GMAIL_NEW_EMAIL", "query": ""}}, "trigger.gmail_new_email"),
    ({"type": "TRIGGER", "config": {"triggerType": "GITHUB_NEW_ISSUE", "repoId": "1"}}, "trigger.github_new_issue"),
    # serviceType이 빠진 저장분도 triggerType만으로 연다(match는 serviceType을 보지 않는다)
    ({"type": "trigger", "config": {"triggerType": "GITHUB_NEW_ISSUE"}}, "trigger.github_new_issue"),
    ({"type": "TRIGGER", "config": {}}, "trigger.manual"),
    ({"type": "TRIGGER", "config": {"triggerType": "MANUAL"}}, "trigger.manual"),
    ({"type": "HTTP", "config": {"url": "x"}}, "http"),
    ({"type": "ACTION", "config": {"tools": [{"name": "slack", "config": {}}]}}, "action.slack_send"),
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


def test_builder_entries_order_most_specific_first():
    ids = [t["id"] for t in tr.builder_entries()]
    assert ids.index("ai.github_query") < ids.index("ai.reasoning") < ids.index("ai.agent")
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


def test_duplicate_builder_match_rejected(tmp_path, monkeypatch):
    for p in glob.glob(os.path.join(tr.TEMPLATES_DIR, "*.json")):
        shutil.copy(p, tmp_path)
    p = tmp_path / "ai.web_search.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["match"] = {"type": "AI", "config.tools.0.name": "builtin:http_fetch", "config.tools.1": None}
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tr, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "_cache", None)
    with pytest.raises(TemplateSchemaError, match="match"):
        tr.load_templates(force=True)


def test_agent_tool_choices_exclude_dynamic_mount_services():
    """AI 노드 GitHub 계약은 tools: [] — 선택지에 github 액션이 있으면 FE가 tools에 넣어 계약이 깨진다."""
    tools = next(f for f in tr.get_template("ai.agent")["inputFields"] if f["key"] == "tools")
    assert not [c for c in tools["choices"] if "github" in c["id"]]
    assert tr.get_template("action.github_list_issues")["builder"] is True   # 카탈로그엔 따로 있다


def test_github_action_nodes_match_their_entries():
    for tid in ("action.github_list_issues", "action.github_create_issue"):
        node = tr.get_template(tid)["fixed"]
        assert tr.match_entry(node)["id"] == tid
