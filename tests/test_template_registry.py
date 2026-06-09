import pytest

from core import template_registry as tr


def test_all_templates_load_and_validate():
    """모든 시드 템플릿이 스키마/드리프트 검증을 통과한다."""
    tr.validate_registry()
    templates = tr.all_templates()
    assert len(templates) >= 4
    ids = {t["id"] for t in templates}
    assert {"trigger.schedule", "ai.notion_create_page", "ai.github_query", "transform"} <= ids


def test_tool_key_matches_tool_map():
    """tool_key가 null이 아닌 템플릿은 모두 _TOOL_MAP에 존재한다(드리프트 없음)."""
    keys = tr._tool_map_keys()
    for tpl in tr.all_templates():
        if tpl["tool_key"] is not None:
            assert tpl["tool_key"] in keys, f"{tpl['id']}: 드리프트"


def test_menu_index_lists_all():
    menu = tr.menu_index()
    for tpl in tr.all_templates():
        assert tpl["id"] in menu


def test_select_by_tags_notion():
    hits = tr.select_by_tags("노션 페이지에 저장해줘")
    ids = [t["id"] for t in hits]
    assert "ai.notion_create_page" in ids


def test_select_by_tags_no_match_returns_empty():
    assert tr.select_by_tags("전혀 무관한 텍스트 zzz") == []


def test_resolve_template_for_ai_tool_node():
    node = {"type": "AI", "config": {"tools": [{"name": "builtin:notion_create_page"}]}}
    tpl = tr.resolve_template_for_node(node)
    assert tpl and tpl["id"] == "ai.notion_create_page"


def test_resolve_template_for_structural_node():
    node = {"type": "TRANSFORM", "config": {"mappings": {}}}
    tpl = tr.resolve_template_for_node(node)
    assert tpl and tpl["id"] == "transform"


def test_golden_snippets_pass_node_validation():
    """모든 golden_snippet은 단독 1노드 워크플로우로서 노드 검증을 통과해야 한다.
    (참조식이 있는 스니펫은 선행 노드가 없어 제외 — #8 전체 회귀에서 통합 검증)"""
    from core.validators.workflow_validator import WorkflowValidator

    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet")
        if not snip:
            continue
        # TRIGGER 스니펫만 단독 검증 가능(나머지는 TRIGGER 시작 규칙/참조 때문에 통합 테스트 영역)
        if snip.get("type") == "TRIGGER":
            WorkflowValidator.validate([snip], [])


# 가공/내부 도구는 독립 노드 템플릿이 아니라 실행 시 자동 부착된다.
_INTERNAL_TOOLS = {
    "builtin:workflow_context", "builtin:json_parse",
    "builtin:text_extract", "builtin:date_format",
}


def test_every_user_facing_tool_has_template():
    """가공/내부 도구를 제외한 모든 _TOOL_MAP 키는 템플릿으로 노출되어야 한다(드리프트 게이트)."""
    keys = tr._tool_map_keys()
    covered = {t["tool_key"] for t in tr.all_templates() if t["tool_key"]}
    expected = keys - _INTERNAL_TOOLS
    missing = expected - covered
    assert not missing, f"템플릿 미커버 tool_key: {sorted(missing)}"


def test_all_node_types_have_at_least_one_template():
    types = {t["node_type"] for t in tr.all_templates()}
    assert {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM"} <= types


def test_schema_rejects_drifted_tool_key():
    """_TOOL_MAP에 없는 tool_key는 스키마 검증에서 거부된다."""
    bad = {
        "id": "ai.fake", "node_type": "AI", "tool_key": "builtin:does_not_exist",
        "tags": ["x"], "menu": "m",
        "fixed": {"type": "AI", "config": {}}, "slots": [],
        "allowed_config_fields": [],
    }
    with pytest.raises(tr.TemplateSchemaError):
        tr._validate_template(bad, "/tmp/ai.fake.json", tr._tool_map_keys())
