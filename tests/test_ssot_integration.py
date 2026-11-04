"""SSOT 통합 + 드리프트 방지 (#8)

노드 템플릿 레지스트리(파일 SSOT) 하나에서 도구 키·검색(태그)이 일관되게 파생되는지,
그리고 드리프트가 생기면 CI가 실패하는지 검증한다.
"""
import pytest

from core import template_registry as tr
from tests.catalog_paths import apps, generation_entries


# --- 드리프트 게이트: 레지스트리 ↔ _TOOL_MAP ↔ 태그 ---


def test_golden_snippet_tool_keys_in_tool_map():
    """레지스트리↔_TOOL_MAP 정합: 골든 스니펫 tools의 키는 모두 실행 레지스트리에 존재한다."""
    keys = tr._tool_map_keys()
    for tpl in generation_entries().values():
        snip = tpl.get("golden_snippet") or {}
        for tool in (snip.get("config", {}) or {}).get("tools", []) or []:
            name = tool.get("name") if isinstance(tool, dict) else tool
            # "mcp"는 동적 도구 센티넬(런타임 catalog 주입)로 _TOOL_MAP에 없어도 허용한다.
            if name == "mcp":
                continue
            assert name in keys, f"{tpl['id']}: 스니펫 도구 '{name}' 드리프트"


def test_every_template_has_tags_and_menu():
    """레지스트리↔검색 정합: 모든 템플릿은 비어있지 않은 tags와 menu를 가진다(검색 가능)."""
    for tpl in generation_entries().values():
        assert tpl["tags"], f"{tpl['id']}: tags 비어있음"
        assert tpl["menu"].strip(), f"{tpl['id']}: menu 비어있음"


def test_drift_detection_fails_on_unknown_tool_key():
    """레지스트리에 _TOOL_MAP 밖 tool_key가 들어오면 검증이 실패한다(CI 드리프트 게이트)."""
    bad = {
        "id": "ai.bogus", "node_type": "AI", "tool_key": "builtin:bogus_tool",
        "tags": ["x"], "menu": "m",
        "fixed": {"type": "AI", "config": {"tools": [{"name": "builtin:bogus_tool"}]}},
    }
    with pytest.raises(tr.TemplateSchemaError, match="드리프트"):
        tr._validate_template(bad, bad["id"], tr._tool_map_keys(), apps())
