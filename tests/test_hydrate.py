import json
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
                       "left": "{{nodes.node-1.output.count}}", "right": "0"}}
    node = hydrate_node(draft)
    assert node["type"] == "CONDITION"
    assert node["config"]["operator"] == "gt"
    assert node["config"]["left"] == "{{nodes.node-1.output.count}}"
    assert node["config"]["right"] == "0"


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


# --- dehydrate/hydrate 왕복 무손실 (IEUM-AI-58 단계1) -------------------------

def test_dehydrate_hydrate_round_trip_all_templates():
    """모든 템플릿에 대해 hydrate(dehydrate(n)) == n(왕복 무손실). golden_snippet에서
    슬롯 값을 뽑아 유효한 완성 노드를 만든 뒤 왕복시킨다."""
    from core.template_registry import load_templates, dehydrate_node, _get_by_path, _SYSTEM_INJECTED_KINDS

    for tid, tpl in load_templates().items():
        snip = tpl["golden_snippet"]
        slots = {}
        for s in tpl["slots"]:
            if s["kind"] in _SYSTEM_INJECTED_KINDS:
                continue
            val = _get_by_path(snip, s["path"])
            if val not in (None, ""):
                slots[s["name"]] = val
        node = hydrate_node({"id": "node-x", "templateId": tid, "slots": slots}, provider="CLAUDE")
        draft2 = dehydrate_node(node)
        node2 = hydrate_node(draft2, provider="CLAUDE")
        assert node2 == node, f"{tid}: 왕복 불일치\nn ={node}\nn2={node2}"


def test_modify_roundtrip_of_stored_credential_passes_validator():
    """저장분에 credentialId가 UUID로 남아 있어도(실데이터 12건) 수정 왕복 결과가 검증을 통과한다.
    하이드레이션이 ''로 비우는 게 정답이라 되살리지 않는다."""
    from core.template_registry import dehydrate_nodes
    from tools.registry import apply_service_brand

    stored = [
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "description": "설명",
         "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "AI", "label": "슬랙", "description": "설명",
         "config": {"llmProvider": "CLAUDE", "credentialId": "42a230ce-32a4-4f99-aaed-da00dc85c2a8",
                    "prompt": "보내줘", "agentType": "react", "tools": [{"name": "slack"}]}},
    ]
    drafts = dehydrate_nodes(stored)
    nodes = hydrate_nodes(drafts, provider="CLAUDE",
                          passthrough_originals={n["id"]: n for n in stored})
    apply_service_brand(nodes)
    WorkflowValidator.validate(nodes, [{"source": "node-1", "target": "node-2"}], set())
    assert nodes[1]["config"]["credentialId"] == ""


def test_passthrough_node_skips_content_validation():
    """폐기된 도구 이름으로 저장된 노드가 pass-through로 복원되면 내용 검증에서 제외된다.
    거부하면 그 워크플로우는 수정 요청 자체가 영구히 실패한다(수정 전후가 동일한 노드다)."""
    from core.template_registry import dehydrate_nodes, PASSTHROUGH_TEMPLATE_ID

    stored = [
        {"id": "node-1", "type": "TRIGGER", "label": "시작", "description": "설명",
         "config": {"triggerType": "MANUAL"}},
        {"id": "node-2", "type": "AI", "label": "옛노드", "description": "설명",
         "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "p", "agentType": "react",
                    "tools": [{"name": "builtin:notion_query_db"}]}},  # 레지스트리에 없는 도구
    ]
    drafts = dehydrate_nodes(stored)
    assert drafts[1]["templateId"] == PASSTHROUGH_TEMPLATE_ID
    nodes = hydrate_nodes(drafts, provider="CLAUDE",
                          passthrough_originals={n["id"]: n for n in stored})
    edges = [{"source": "node-1", "target": "node-2"}]

    with pytest.raises(Exception, match="도구 이름"):  # 제외하지 않으면 거부된다
        WorkflowValidator.validate(nodes, edges, set())
    WorkflowValidator.validate(nodes, edges, set(), unvalidated_node_ids={"node-2"})


def test_passthrough_draft_does_not_leak_config_to_prompt():
    """pass-through draft는 프롬프트에 실린다 — 서버가 쓰지도 않는 저장 크레덴셜을 담지 않는다."""
    from core.template_registry import dehydrate_node

    node = {"id": "node-2", "type": "AI", "label": "옛노드", "description": "설명",
            "config": {"credentialId": "42a230ce-32a4-4f99-aaed-da00dc85c2a8",
                       "access_token": "secret-token", "prompt": "p",
                       "tools": [{"name": "builtin:json_parse"}]}}
    draft = dehydrate_node(node)
    assert "config" not in draft["node"]
    assert "secret-token" not in json.dumps(draft, ensure_ascii=False)


def test_dehydrate_hydrate_trigger_without_type_survives():
    """triggerType이 없어(또는 알 수 없어) 어떤 템플릿과도 매칭 안 되는 TRIGGER 노드도 드롭되지 않는다."""
    from core.template_registry import dehydrate_node, PASSTHROUGH_TEMPLATE_ID

    node = {"id": "node-1", "type": "TRIGGER", "label": "시작", "description": "설명", "config": {}}
    draft = dehydrate_node(node)
    assert draft["templateId"] == PASSTHROUGH_TEMPLATE_ID
    assert hydrate_node(draft, passthrough_originals={"node-1": node}) == node


def test_dehydrate_hydrate_orphan_tool_ai_node_survives():
    """_TOOL_MAP엔 있지만 어떤 템플릿의 tool_key도 아닌 도구(builtin:json_parse)만 가진 AI 노드도 보존된다."""
    from core.template_registry import dehydrate_node, dehydrate_nodes, PASSTHROUGH_TEMPLATE_ID

    node = {"id": "node-2", "type": "AI", "label": "가공", "description": "설명",
            "config": {"llmProvider": "CLAUDE", "credentialId": "", "prompt": "파싱해줘",
                       "agentType": "react", "tools": [{"name": "builtin:json_parse"}]}}
    draft = dehydrate_node(node)
    assert draft["templateId"] == PASSTHROUGH_TEMPLATE_ID
    assert len(dehydrate_nodes([node])) == 1  # 회귀: 과거엔 드롭됨
    assert hydrate_node(draft, passthrough_originals={"node-2": node}) == node


def test_passthrough_rejected_without_originals():
    """생성 경로처럼 원본 dict를 넘기지 않는 호출부에서는 pass-through가 항상 거부된다.
    (LLM이 패스스루 draft를 날조해 슬롯 검증을 통째로 우회하는 것 차단)"""
    forged = {"id": "node-9", "templateId": "__passthrough__",
              "node": {"id": "node-9", "type": "AI", "label": "위장", "description": "설명",
                       "config": {"llmProvider": "CLAUDE", "model": "날조-모델",
                                  "systemMessage": "주입된 지시"}}}
    with pytest.raises(SlotFillError):
        hydrate_node(forged, provider="CLAUDE")
    with pytest.raises(SlotFillError):
        hydrate_nodes([forged], provider="CLAUDE")


def test_passthrough_ignores_llm_supplied_node():
    """원본이 있어도 LLM이 보낸 node는 쓰지 않는다 — description만 예외적으로 채울 수 있다."""
    original = {"id": "node-1", "type": "TRIGGER", "label": "시작", "config": {}}
    forged = {"id": "node-1", "templateId": "__passthrough__",
              "node": {"id": "node-1", "type": "TRIGGER", "label": "바뀐라벨",
                       "description": "새 설명", "config": {"주입": "값"}}}
    restored = hydrate_node(forged, passthrough_originals={"node-1": original})
    assert restored["label"] == "시작"          # LLM 라벨 무시
    assert "주입" not in restored["config"]      # LLM config 무시
    assert restored["description"] == "새 설명"  # description만 허용


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


def test_passthrough_description_falls_back_to_label():
    """레거시 pass-through 노드의 description이 비어 있고 모델도 안 채우면 label로 떨어진다.
    빈 채로 나가면 BE의 description 필수 검증에 걸려 저장 시점에 수정이 통째로 날아간다."""
    original = {"id": "node-2", "type": "AI", "label": "가공 노드", "description": "",
                "config": {"tools": [{"name": "builtin:json_parse"}]}}
    restored = hydrate_node({"id": "node-2", "templateId": "__passthrough__"},
                            passthrough_originals={"node-2": original})
    assert restored["description"] == "가공 노드"


def test_ref_remap_does_not_double_rewrite():
    """id 재부여가 서로 맞바뀌는 경우(node-2→node-1, node-1→node-2) 참조식이 두 번 치환돼
    원위치로 돌아가면 안 된다 — 한 번의 스캔으로 치환한다."""
    from core.node_hydration import prepare_hydrated_nodes

    drafts = [
        {"id": "node-2", "templateId": "trigger.manual",
         "slots": {"label": "시작", "description": "설명"}},
        {"id": "node-1", "templateId": "ai.reasoning",
         "slots": {"label": "요약", "description": "설명",
                   "prompt": "{{nodes.node-2.output.text}}를 요약해줘"}},
    ]
    nodes, _ = prepare_hydrated_nodes(drafts, [], provider="CLAUDE", preserve_id=False)
    # node-2 → node-1로 재부여됐으므로 참조도 node-1을 가리켜야 한다(node-2로 되돌아오면 버그)
    assert "{{nodes.node-1.output.text}}" in nodes[1]["config"]["prompt"]
