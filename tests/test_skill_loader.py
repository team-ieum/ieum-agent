import pytest
from core.skill_loader import format_mcp_catalog


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
