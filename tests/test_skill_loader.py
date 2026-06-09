import pytest
from core.skill_loader import load_design_rules, format_mcp_catalog


def test_format_mcp_catalog_empty_returns_blank():
    assert format_mcp_catalog(None) == ""
    assert format_mcp_catalog([]) == ""


def test_format_mcp_catalog_lists_servers_with_catalog_id():
    text = format_mcp_catalog([
        {"catalogId": "cat-1", "name": "사내 위키", "description": "위키 검색"},
        {"catalogId": "cat-2", "name": "DB MCP", "description": None},
    ])
    assert "cat-1" in text and "사내 위키" in text and "위키 검색" in text
    assert "cat-2" in text and "DB MCP" in text
    assert "mcp:<catalogId>" in text  # 사용 형식 안내 포함


def test_format_mcp_catalog_skips_entries_without_catalog_id():
    text = format_mcp_catalog([{"name": "이름만", "description": "x"}])
    assert text == ""


def test_load_design_rules_common_included():
    # 어떤 프롬프트를 넣든 공통 가이드(node-types, bad-examples)가 포함되어야 함
    rules = load_design_rules("아무 요구나 해줘")
    assert "Node Types" in rules
    assert "Bad Examples" in rules


def test_load_design_rules_always_includes_tool_catalog():
    # T2: 도구 카탈로그(tool-selection.md)는 프롬프트와 무관하게 항상 포함된다
    rules = load_design_rules("노션 페이지에 오늘 일기 적어줘")
    assert "builtin:notion_create_page" in rules
    assert "builtin:notion_read_page" in rules


def test_load_design_rules_injects_catalog_for_unmatched_keyword():
    # T2 회귀: 키워드 갭이 있어도 도구 메뉴 인덱스가 항상 주입되어 도구 존재가 누락되지 않는다.
    rules = load_design_rules("매일 이메일로 요약 보내줘")
    assert "gmail" in rules.lower()              # '이메일' 태그로 검색층 매칭
    assert "google_sheets" in rules.lower()      # 메뉴 인덱스에 항상 노출
    assert "사용 가능한 노드/도구 메뉴" in rules


def test_load_design_rules_works_without_prompt_arg():
    # prompt 인자 없이 호출해도 정상 동작한다 (하위 호환)
    rules = load_design_rules()
    assert isinstance(rules, str)
    assert "builtin:notion_create_page" in rules


def test_relevant_selection_smaller_than_fallback():
    """관련 태그가 매칭되면 검색층이 좁아져 무관 요청(전체 폴백)보다 짧아야 한다(토큰 절감)."""
    focused = load_design_rules("노션 페이지에 저장해줘")
    fallback = load_design_rules("대충 아무거나 만들어줘")
    assert len(focused) < len(fallback)


def test_menu_index_always_present():
    """검색 매칭과 무관하게 도구 메뉴 인덱스와 구조 노드 예시는 항상 주입된다."""
    rules = load_design_rules("zzz 무관한 텍스트")
    assert "사용 가능한 노드/도구 메뉴" in rules
    assert "구조 노드 예시" in rules


def test_modify_request_includes_current_node_templates():
    """수정 요청 시 현재 노드가 쓰는 도구 템플릿이 검색층에 포함된다."""
    current = [{"type": "AI", "config": {"tools": [{"name": "slack"}]}}]
    rules = load_design_rules("메시지 문구만 바꿔줘", current_nodes=current)
    assert "ai.slack_send" in rules
