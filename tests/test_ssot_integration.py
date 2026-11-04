"""SSOT 통합 + 드리프트 방지 (#8)

노드 템플릿 레지스트리(파일 SSOT) 하나에서 도구 키·검색(태그)이 일관되게 파생되는지,
그리고 드리프트가 생기면 CI가 실패하는지 검증한다.
"""
import pytest

from core import template_registry as tr
from tests.catalog_paths import apps


# --- 드리프트 게이트: 레지스트리 ↔ _TOOL_MAP ↔ 태그 ---


def test_drift_detection_fails_on_unknown_tool_key():
    """레지스트리에 _TOOL_MAP 밖 tool_key가 들어오면 검증이 실패한다(CI 드리프트 게이트)."""
    bad = {
        "id": "ai.bogus", "node_type": "AI", "tool_key": "builtin:bogus_tool",
        "fixed": {"type": "AI", "config": {"tools": [{"name": "builtin:bogus_tool"}]}},
    }
    with pytest.raises(tr.TemplateSchemaError, match="드리프트"):
        tr._validate_template(bad, bad["id"], tr._tool_map_keys(), apps())
