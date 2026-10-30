from core import template_registry as tr


def test_all_templates_load_and_validate():
    """모든 시드 템플릿이 스키마/드리프트 검증을 통과한다."""
    tr.validate_registry()
    templates = tr.all_templates()
    assert len(templates) >= 4
    ids = {t["id"] for t in templates}
    assert {"schedule.every", "ai.notion_create_page", "ai.github_query", "transform.map"} <= ids


def test_tool_key_matches_tool_map():
    """tool_key가 null이 아닌 템플릿은 모두 _TOOL_MAP에 존재한다(드리프트 없음)."""
    keys = tr._tool_map_keys()
    for tpl in tr.all_templates():
        if tpl["tool_key"] is not None:
            assert tpl["tool_key"] in keys, f"{tpl['id']}: 드리프트"


def test_select_by_tags_notion():
    hits = tr.select_by_tags("노션 페이지에 저장해줘")
    ids = [t["id"] for t in hits]
    assert "ai.notion_create_page" in ids


def test_select_by_tags_no_match_returns_empty():
    assert tr.select_by_tags("전혀 무관한 텍스트 zzz") == []


def test_resolve_template_for_ai_tool_node():
    node = {"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "builtin:notion_create_page"}]}}
    tpl = tr.resolve_template_for_node(node)
    assert tpl and tpl["id"] == "ai.notion_create_page"


def test_resolve_template_for_structural_node():
    node = {"type": "TRANSFORM", "event": "transform.map", "config": {"mappings": {}}}
    tpl = tr.resolve_template_for_node(node)
    assert tpl and tpl["id"] == "transform.map"


# 가공/내부 도구는 독립 노드 템플릿이 아니라 실행 시 자동 부착된다.
_INTERNAL_TOOLS = {
    "builtin:workflow_context", "builtin:json_parse",
    "builtin:text_extract", "builtin:date_format",
}

# 생성 프리셋 없이 ai.agent 도구 선택지(이벤트 title)로만 AI 노드에 싣는 도구(spec 2026-10-20 §9 — 프리셋을 만들지 않는다).
# 생성(채팅) 경로는 이 도구로 노드를 만들지 못한다.
_NO_PRESET_TOOLS = {"builtin:slack_send_channel_message", "builtin:discord_send_channel_message",
                    "builtin:notion_create_row", "builtin:notion_update_row"}  # spec 2026-10-26 §1: 프리셋 없음


def test_every_user_facing_tool_has_template():
    """가공/내부 도구를 제외한 모든 _TOOL_MAP 키는 템플릿으로 노출되어야 한다(드리프트 게이트)."""
    keys = tr._tool_map_keys()
    covered = {t["tool_key"] for t in tr.all_templates() if t["tool_key"]}
    expected = keys - _INTERNAL_TOOLS - _NO_PRESET_TOOLS
    missing = expected - covered
    assert not missing, f"템플릿 미커버 tool_key: {sorted(missing)}"


def test_all_node_types_have_at_least_one_template():
    types = {t["node_type"] for t in tr.all_templates()}
    assert {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM", "APPROVAL"} <= types


def test_dehydrate_node_리스트_아닌_tools는_TypeError_없이_pass_through():
    from core.template_registry import dehydrate_node, PASSTHROUGH_TEMPLATE_ID
    node = {"id": "n2", "type": "ACTION", "event": "jira.create_issue", "label": "x", "config": {"tools": 1}}
    assert dehydrate_node(node)["templateId"] == PASSTHROUGH_TEMPLATE_ID


def test_allowed_config_fields_AI_노드는_매칭된_템플릿만_허용한다():
    """event가 있는 AI 노드(운영 경로: hydrate가 event를 채운다)는 매칭된 템플릿의 칸만 허용한다 — node_type 합집합 폴백이
    아니다. outputFields는 ai.reasoning 전용이라 web_search 프리셋 노드(event=ai.agent)에는 허용되지 않는다."""
    web = {"type": "AI", "event": "ai.agent", "config": {"tools": [{"name": "builtin:web_search"}]}}
    assert "outputFields" not in tr.allowed_config_fields_for_node(web)
    assert "outputFields" in tr.allowed_config_fields_for_node({"type": "AI", "event": "ai.reasoning", "config": {}})
