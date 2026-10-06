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
    ({"type": "AI", "config": {"agentType": "react", "tools": [], "serviceType": "GITHUB"}}, "ai.github_query"),
    # 도구 없이 저장된 ai.agent 골격(react, serviceType 없음)은 GitHub 폼이 아니라 추론 폼으로 연다
    ({"type": "AI", "config": {"agentType": "react", "tools": []}}, "ai.reasoning"),
    ({"type": "AI", "config": {"tools": [{"name": "mcp", "config": {"catalogId": "c"}}]}}, "ai.mcp"),
    ({"type": "AI", "config": {"tools": [{"name": "builtin:web_search"}]}}, "ai.web_search"),
    # 도구 2개 이상이면 첫 도구 전용 폼이 아니라 AI 에이전트 폼으로 연다
    ({"type": "AI", "config": {"tools": [{"name": "builtin:web_search"}, {"name": "discord"}]}}, "ai.agent"),
    ({"type": "AI", "config": {"tools": [{"name": "mcp", "config": {"catalogId": "c"}}, {"name": "discord"}]}},
     "ai.agent"),
    ({"type": "TRIGGER", "config": {"triggerType": "SCHEDULE", "cron": "0 9 * * *"}}, "trigger.schedule"),
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
    agent = tr.get_template("ai.agent")
    tools = next(f for f in agent["inputFields"] if f["key"] == "tools")
    actions = {t["tool_key"]: t["title"] for t in tr.all_entries() if t["node_type"] == "ACTION" and t["builder"]}
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
