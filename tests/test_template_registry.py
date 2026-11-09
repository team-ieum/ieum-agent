from core import template_registry as tr


def test_all_templates_load_and_validate():
    """모든 시드 템플릿이 스키마/드리프트 검증을 통과한다."""
    tr.validate_registry()
    ids = set(tr.load_templates())
    assert {"schedule.every", "ai.web_search", "ai.github_query", "transform.map"} <= ids


def test_tool_key_matches_tool_map():
    """tool_key가 null이 아닌 템플릿은 모두 _TOOL_MAP에 존재한다(드리프트 없음)."""
    keys = tr._tool_map_keys()
    for tpl in tr.load_templates().values():
        if tpl["tool_key"] is not None and tpl["node_type"] != "ACTION":  # ACTION은 builtin:github_* 등 전용 키도 쓴다
            assert tpl["tool_key"] in keys, f"{tpl['id']}: 드리프트"


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
    covered = {t["tool_key"] for t in tr.load_templates().values() if t["tool_key"]}
    expected = keys - _INTERNAL_TOOLS - _NO_PRESET_TOOLS
    missing = expected - covered
    assert not missing, f"템플릿 미커버 tool_key: {sorted(missing)}"


def test_all_node_types_have_at_least_one_template():
    types = {t["node_type"] for t in tr.load_templates().values()}
    assert {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM", "APPROVAL"} <= types


def _delay_tpl(node_type="DELAY"):
    return {"id": "delay.for", "node_type": node_type, "kind": "action", "tool_key": None,
            "title": "일정 시간 기다리기", "description": "정한 시간만큼 기다려요",
            "fixed": {"type": node_type, "config": {}}, "inputFields": []}


def test_delay_node_type_is_valid():
    tr._validate_template(_delay_tpl(), "delay.for", tr._tool_map_keys(), {})


def test_unknown_node_type_still_rejected():
    import pytest
    with pytest.raises(tr.TemplateSchemaError):
        tr._validate_template(_delay_tpl("WAIT"), "delay.for", tr._tool_map_keys(), {})
