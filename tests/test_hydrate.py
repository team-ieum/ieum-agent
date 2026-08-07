import pytest

from core.template_registry import (
    hydrate_node,
    hydrate_nodes,
    _set_by_path,
    SlotFillError,
)
from core.validators.workflow_validator import WorkflowValidator


# --- _set_by_path -------------------------------------------------------------

def test_set_by_path_top_level():
    obj = {}
    _set_by_path(obj, "label", "노드")
    assert obj["label"] == "노드"


def test_set_by_path_nested_dict():
    obj = {"config": {}}
    _set_by_path(obj, "config.prompt", "지시")
    assert obj["config"]["prompt"] == "지시"


def test_set_by_path_list_index():
    obj = {"config": {"tools": [{"name": "mcp", "config": {}}]}}
    _set_by_path(obj, "config.tools.0.config.catalogId", "srv-1")
    assert obj["config"]["tools"][0]["config"]["catalogId"] == "srv-1"


# --- hydrate_node: 정상 -------------------------------------------------------

def test_hydrate_service_ai_node():
    draft = {"id": "node-2", "templateId": "ai.notion_search",
             "slots": {"label": "검색", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "트렌드 검색"}}
    node = hydrate_node(draft, provider="CLAUDE")
    assert node["type"] == "AI"
    assert node["label"] == "검색"
    assert node["config"]["agentType"] == "react"           # fixed
    assert node["config"]["credentialId"] == ""             # fixed
    assert node["config"]["tools"] == [{"name": "builtin:notion_search"}]  # fixed
    assert node["config"]["llmProvider"] == "CLAUDE"        # provider 자동 주입
    assert node["config"]["prompt"] == "트렌드 검색"


def test_hydrate_provider_auto_injected_overrides_slot():
    draft = {"templateId": "ai.notion_search",
             "slots": {"label": "x", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "y", "llmProvider": "OPENAI"}}
    node = hydrate_node(draft, provider="CLAUDE")
    assert node["config"]["llmProvider"] == "CLAUDE"  # 인자 provider가 우선


def test_hydrate_condition_node():
    draft = {"templateId": "condition",
             "slots": {"label": "분기", "description": "이 노드가 하는 일을 쉽게 설명해요.", "operator": "gt",
                       "leftValue": "{{nodes.node-1.output.count}}", "rightValue": "0"}}
    node = hydrate_node(draft)
    assert node["type"] == "CONDITION"
    assert node["config"]["operator"] == "gt"
    assert node["config"]["leftValue"] == "{{nodes.node-1.output.count}}"
    assert node["config"]["rightValue"] == "0"


def test_hydrate_http_optional_slot_skipped():
    draft = {"templateId": "http",
             "slots": {"label": "호출", "description": "이 노드가 하는 일을 쉽게 설명해요.", "method": "GET", "url": "https://api.example.com"}}
    node = hydrate_node(draft)
    assert node["config"]["method"] == "GET"
    assert "headers" not in node["config"]  # optional 미제공 → 스킵
    assert "body" not in node["config"]


def test_hydrate_schedule_trigger():
    draft = {"templateId": "trigger.schedule",
             "slots": {"label": "매일 9시", "description": "이 노드가 하는 일을 쉽게 설명해요.", "cron": "0 9 * * *"}}
    node = hydrate_node(draft)
    assert node["type"] == "TRIGGER"
    assert node["config"]["triggerType"] == "SCHEDULE"  # fixed
    assert node["config"]["cron"] == "0 9 * * *"


def test_hydrate_reasoning_node():
    draft = {"templateId": "ai.reasoning", "slots": {"label": "요약", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "결과를 요약해줘"}}
    node = hydrate_node(draft, provider="CLAUDE")
    assert node["type"] == "AI"
    assert node["config"]["agentType"] == "simple"
    assert node["config"]["tools"] == []
    assert node["config"]["prompt"] == "결과를 요약해줘"


def test_resolve_toolless_ai_disambiguates_by_agent_type():
    # 도구 없는 AI 노드: agentType으로 github(react) vs reasoning(simple) 구분
    from core.template_registry import resolve_template_for_node
    reasoning_node = {"type": "AI", "config": {"agentType": "simple", "tools": []}}
    github_node = {"type": "AI", "config": {"agentType": "react", "tools": []}}
    assert resolve_template_for_node(reasoning_node)["id"] == "ai.reasoning"
    assert resolve_template_for_node(github_node)["id"] == "ai.github_query"


def test_dehydrate_reasoning_round_trip():
    from core.template_registry import dehydrate_node
    node = {"id": "node-3", "type": "AI",
            "config": {"agentType": "simple", "credentialId": "", "tools": [],
                       "llmProvider": "CLAUDE", "prompt": "요약해줘"}}
    draft = dehydrate_node(node)
    assert draft["templateId"] == "ai.reasoning"
    assert draft["slots"]["prompt"] == "요약해줘"
    assert "llmProvider" not in draft["slots"]


def test_hydrate_mcp_pseudo_template_nested_catalog_id():
    draft = {"templateId": "ai.mcp",
             "slots": {"label": "MCP", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "도구 호출", "catalogId": "srv-1"}}
    node = hydrate_node(draft, provider="CLAUDE")
    tool = node["config"]["tools"][0]
    assert tool["name"] == "mcp"
    assert tool["config"]["catalogId"] == "srv-1"  # 중첩 list 경로 주입


def test_dehydrate_mcp_node_round_trip_not_dropped():
    # MODIFY 시 저장된 MCP 노드(tool_key 없는 'mcp' 센티넬)가 dehydrate에서 드롭되지 않아야 한다.
    from core.template_registry import dehydrate_node, dehydrate_nodes
    node = {"id": "node-2", "type": "AI",
            "config": {"agentType": "react", "credentialId": "", "llmProvider": "CLAUDE",
                       "prompt": "도구 호출", "tools": [{"name": "mcp", "config": {"catalogId": "srv-1"}}]}}
    draft = dehydrate_node(node)
    assert draft is not None  # 회귀: 과거엔 None으로 드롭됨
    assert draft["templateId"] == "ai.mcp"
    assert draft["slots"]["catalogId"] == "srv-1"
    assert draft["slots"]["prompt"] == "도구 호출"
    assert len(dehydrate_nodes([node])) == 1  # 리스트 역변환에서도 보존


# --- hydrate_node: 에러(환각 차단) -------------------------------------------

def test_hydrate_unknown_template_rejected():
    with pytest.raises(SlotFillError):
        hydrate_node({"templateId": "ai.does_not_exist", "slots": {}})


def test_hydrate_missing_required_slot_rejected():
    with pytest.raises(SlotFillError):
        hydrate_node({"templateId": "ai.notion_search", "slots": {"label": "x", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
                     provider="CLAUDE")  # prompt 누락


def test_hydrate_unknown_slot_rejected():
    with pytest.raises(SlotFillError):
        hydrate_node({"templateId": "ai.notion_search",
                      "slots": {"label": "x", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "y", "hacked": "z"}},
                     provider="CLAUDE")


def test_hydrate_provider_required_without_provider():
    with pytest.raises(SlotFillError):
        hydrate_node({"templateId": "ai.notion_search",
                      "slots": {"label": "x", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "y"}})  # provider 미지정


# --- hydrate_nodes + 통합 검증 ------------------------------------------------

def test_hydrate_nodes_assigns_sequential_ids():
    drafts = [
        {"templateId": "trigger.manual", "slots": {"label": "시작", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
        {"templateId": "ai.notion_search", "slots": {"label": "검색", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "p"}},
    ]
    nodes = hydrate_nodes(drafts, provider="CLAUDE")
    assert [n["id"] for n in nodes] == ["node-1", "node-2"]


def test_hydrated_workflow_passes_validator():
    drafts = [
        {"templateId": "trigger.manual", "slots": {"label": "시작", "description": "이 노드가 하는 일을 쉽게 설명해요."}},
        {"templateId": "ai.notion_search", "slots": {"label": "검색", "description": "이 노드가 하는 일을 쉽게 설명해요.", "prompt": "노션 검색"}},
    ]
    nodes = hydrate_nodes(drafts, provider="CLAUDE")
    edges = [{"source": "node-1", "target": "node-2"}]
    # hydrate 결과가 기존 의미 검증을 통과해야 한다(브랜드 주입 포함)
    from tools.registry import apply_service_brand
    apply_service_brand(nodes)
    WorkflowValidator.validate(nodes, edges, set())
    assert nodes[1]["config"]["brand"] == "notion"
