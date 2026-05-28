import pytest
from core.skill_loader import load_design_rules

def test_load_design_rules_common_included():
    # 어떤 프롬프트를 넣든 공통 가이드(node-types, bad-examples)가 포함되어야 함
    rules = load_design_rules("아무 요구나 해줘")
    assert "Node Types" in rules
    assert "Bad Examples" in rules
    # Notion 전용 도구 지침은 포함되지 않아야 함
    assert "notion_read_page" not in rules

def test_load_design_rules_notion_included():
    # 'notion' 키워드가 프롬프트에 들어가면 Notion 관련 가이드가 포함되어야 함
    rules = load_design_rules("노션 페이지에 오늘 일기 적어줘")
    assert "notion_read_page" in rules
    assert "notion_create_page" in rules
    # Slack 지침은 포함되지 않아야 함
    assert "google_sheets_read" not in rules

def test_load_design_rules_multiple_included():
    # 여러 키워드가 감지되면 해당 가이드들이 모두 포함되어야 함
    rules = load_design_rules("구글 시트를 읽어서 슬랙으로 알림 보내줘")
    assert "google_sheets_read" in rules
    assert "slack" in rules.lower()
