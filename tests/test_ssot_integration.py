"""SSOT 통합 + 드리프트 방지 (#8)

노드 템플릿 레지스트리(파일 SSOT) 하나에서 프롬프트(skill_loader)·검증(validator)·
검색(태그)·DB(seed)가 일관되게 파생되는지, 그리고 드리프트가 생기면 CI가 실패하는지 검증한다.
"""
import pytest

from core import template_registry as tr
from core.skill_loader import load_design_rules, _ALWAYS_REFERENCE_FILES
from core.validators.workflow_validator import WorkflowValidator, WorkflowValidationError


# --- 드리프트 게이트: 레지스트리 ↔ _TOOL_MAP ↔ 검증 ↔ 태그 ---

def test_golden_snippet_config_within_validator_whitelist():
    """레지스트리↔검증 정합: 모든 골든 스니펫의 config 키는 validator 화이트리스트 안에 있어야 한다.
    (SCHEMA.md 규칙 4/5 — 템플릿이 보여주는 예시가 곧 검증을 통과해야 함)"""
    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet")
        if not snip:
            continue
        allowed = tr.allowed_config_fields_for_node(snip)
        assert allowed is not None, f"{tpl['id']}: 노드 타입 화이트리스트 없음"
        unknown = set((snip.get("config") or {}).keys()) - allowed
        assert not unknown, f"{tpl['id']}: 골든 스니펫 config가 화이트리스트 위반 {sorted(unknown)}"


def test_golden_snippet_tool_keys_in_tool_map():
    """레지스트리↔_TOOL_MAP 정합: 골든 스니펫 tools의 키는 모두 실행 레지스트리에 존재한다."""
    keys = tr._tool_map_keys()
    for tpl in tr.all_templates():
        snip = tpl.get("golden_snippet") or {}
        for tool in (snip.get("config", {}) or {}).get("tools", []) or []:
            name = tool.get("name") if isinstance(tool, dict) else tool
            # "mcp"는 동적 도구 센티넬(런타임 catalog 주입)로 _TOOL_MAP에 없어도 허용한다.
            if name == "mcp":
                continue
            assert name in keys, f"{tpl['id']}: 스니펫 도구 '{name}' 드리프트"


def test_ai_golden_snippets_pass_field_validation():
    """레지스트리↔검증(#7) 정합: AI 골든 스니펫의 enum/필수값이 validator를 통과한다."""
    for tpl in tr.all_templates():
        if tpl["node_type"] != "AI":
            continue
        snip = tpl.get("golden_snippet")
        if not snip:
            continue
        # 예외가 발생하면 실패
        WorkflowValidator._validate_ai_node_fields(snip.get("config", {}), snip.get("id", "x"))


def test_every_template_has_tags_and_menu():
    """레지스트리↔검색 정합: 모든 템플릿은 비어있지 않은 tags와 menu를 가진다(검색 가능)."""
    for tpl in tr.all_templates():
        assert tpl["tags"], f"{tpl['id']}: tags 비어있음"
        assert tpl["menu"].strip(), f"{tpl['id']}: menu 비어있음"


# --- 통합: 한 레지스트리가 세 소비처를 구동 ---

def test_registry_drives_skill_loader_menu():
    """skill_loader 메뉴 인덱스가 레지스트리의 모든 템플릿을 노출한다."""
    rules = load_design_rules("zzz 무관")  # 폴백 경로
    for tpl in tr.all_templates():
        assert tpl["id"] in rules, f"{tpl['id']}가 메뉴에 누락"


def test_dead_reference_files_not_injected():
    """통짜 good-examples.md / tool-selection.md는 더 이상 항상층에 주입되지 않는다.
    (레지스트리 검색층으로 대체 — 누군가 되돌리면 실패시켜 회귀를 막음)"""
    assert "good-examples.md" not in _ALWAYS_REFERENCE_FILES
    assert "tool-selection.md" not in _ALWAYS_REFERENCE_FILES


# --- 환각 회귀: 골든 워크플로우가 새 파이프라인(#5/#6/#7 포함)을 통과 ---

def _wf_from_structural_and_tool(struct_tpl_id, tool_tpl_id):
    """구조 트리거 + 도구 노드 1개로 최소 워크플로우를 조립한다(참조식 없는 스니펫만)."""
    trigger = dict(tr.get_template("trigger.schedule")["golden_snippet"])
    trigger["id"] = "node-1"
    tool_snip = dict(tr.get_template(tool_tpl_id)["golden_snippet"])
    tool_snip = {**tool_snip, "id": "node-2",
                 "config": {k: v for k, v in tool_snip["config"].items()}}
    return [trigger, tool_snip], [{"source": "node-1", "target": "node-2"}]


def test_no_reference_tool_snippet_validates_end_to_end():
    """참조식이 없는 도구 스니펫(web_search)은 트리거와 결합해 전체 검증을 통과한다."""
    nodes, edges = _wf_from_structural_and_tool("trigger.schedule", "ai.web_search")
    WorkflowValidator.validate(nodes, edges)


def test_drift_detection_fails_on_unknown_tool_key():
    """레지스트리에 _TOOL_MAP 밖 tool_key가 들어오면 검증이 실패한다(CI 드리프트 게이트)."""
    bad = {
        "id": "ai.bogus", "node_type": "AI", "tool_key": "builtin:bogus_tool",
        "tags": ["x"], "menu": "m",
        "fixed": {"type": "AI", "config": {"tools": [{"name": "builtin:bogus_tool"}]}},
        "slots": [], "allowed_config_fields": ["tools"],
    }
    with pytest.raises(tr.TemplateSchemaError):
        tr._validate_template(bad, "/tmp/ai.bogus.json", tr._tool_map_keys())
