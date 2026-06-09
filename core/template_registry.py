"""노드 템플릿 레지스트리 (SSOT 로더)

ieum-workflow-design/templates/*.json 을 단일 진실 원천으로 로드·검증한다.
스키마 명세는 templates/SCHEMA.md 참고.

소비처:
- skill_loader(#4): menu_index() + select_by_tags() 로 항상층/검색층 주입
- workflow_validator(#5): allowed_config_fields() 로 config 필드 화이트리스트
- seed 스크립트(#3): all_templates() 를 MongoDB로 동기화
"""
import os
import json
import glob
import logging

logger = logging.getLogger(__name__)

TEMPLATES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ieum-workflow-design", "templates")
)

# 플랫폼이 모든 노드 config에 주입/정규화하는 공통 필드(_normalize_node 참고).
# 노드 타입과 무관하게 허용된다(런타임 주입 또는 리소스 ID 플레이스홀더).
UNIVERSAL_CONFIG_FIELDS = {
    "credentialId", "access_token", "parent_page_id", "spreadsheet_id", "calendar_id",
}

_VALID_NODE_TYPES = {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM"}
_VALID_SLOT_KINDS = {"string", "enum", "provider", "cron", "expr", "mapping", "http_method"}
_REQUIRED_TOP_KEYS = {"id", "node_type", "tool_key", "tags", "menu", "fixed", "slots", "allowed_config_fields"}

# 모듈 캐시 (파일은 기동 중 불변)
_cache: dict | None = None


class TemplateSchemaError(ValueError):
    """템플릿 스키마/드리프트 검증 실패"""
    pass


def _tool_map_keys() -> set:
    """_TOOL_MAP 키 집합. tools 패키지는 google.adk를 import하므로 lazy import한다."""
    from tools import _TOOL_MAP
    return set(_TOOL_MAP.keys())


def _validate_template(tpl: dict, filename: str, tool_keys: set) -> None:
    """단일 템플릿의 스키마와 _TOOL_MAP 드리프트를 검증한다."""
    if not isinstance(tpl, dict):
        raise TemplateSchemaError(f"{filename}: 템플릿 루트는 JSON 객체(dict)여야 합니다.")
    missing = _REQUIRED_TOP_KEYS - tpl.keys()
    if missing:
        raise TemplateSchemaError(f"{filename}: 필수 필드 누락 {sorted(missing)}")
    if not isinstance(tpl.get("allowed_config_fields"), list):
        raise TemplateSchemaError(f"{tpl.get('id', filename)}: allowed_config_fields는 리스트여야 합니다.")
    if not isinstance(tpl.get("slots"), list):
        raise TemplateSchemaError(f"{tpl.get('id', filename)}: slots는 리스트여야 합니다.")

    tid = tpl["id"]
    expected_file = f"{tid}.json"
    if os.path.basename(filename) != expected_file:
        raise TemplateSchemaError(f"{filename}: id '{tid}'와 파일명이 불일치(기대: {expected_file})")

    if tpl["node_type"] not in _VALID_NODE_TYPES:
        raise TemplateSchemaError(f"{tid}: node_type '{tpl['node_type']}' 유효하지 않음 {sorted(_VALID_NODE_TYPES)}")

    if not isinstance(tpl["tags"], list) or not tpl["tags"]:
        raise TemplateSchemaError(f"{tid}: tags는 비어있지 않은 리스트여야 함")

    # tool_key 드리프트 검증
    tool_key = tpl["tool_key"]
    if tool_key is not None and tool_key not in tool_keys:
        raise TemplateSchemaError(f"{tid}: tool_key '{tool_key}'가 _TOOL_MAP에 없음(드리프트)")

    fixed = tpl["fixed"]
    if not isinstance(fixed, dict) or "type" not in fixed:
        raise TemplateSchemaError(f"{tid}: fixed에 type이 필요함")
    if fixed["type"] != tpl["node_type"]:
        raise TemplateSchemaError(f"{tid}: fixed.type({fixed['type']}) != node_type({tpl['node_type']})")

    fixed_config = fixed.get("config", {}) or {}

    # fixed.config.tools[*].name 도 _TOOL_MAP에 존재해야 함
    for tool in fixed_config.get("tools", []) or []:
        name = tool.get("name") if isinstance(tool, dict) else tool
        if name and name not in tool_keys:
            raise TemplateSchemaError(f"{tid}: fixed tools '{name}'가 _TOOL_MAP에 없음(드리프트)")

    # slots 검증
    allowed = set(tpl["allowed_config_fields"])
    for slot in tpl["slots"]:
        for k in ("name", "path", "required", "kind"):
            if k not in slot:
                raise TemplateSchemaError(f"{tid}: slot에 '{k}' 누락 ({slot})")
        if slot["kind"] not in _VALID_SLOT_KINDS:
            raise TemplateSchemaError(f"{tid}: slot kind '{slot['kind']}' 유효하지 않음")
        # config.* slot은 allowed_config_fields에 포함되어야 함
        path = slot["path"]
        if path.startswith("config."):
            key = path.split(".")[1]
            if key not in allowed:
                raise TemplateSchemaError(f"{tid}: slot '{slot['name']}'의 config.{key}가 allowed_config_fields에 없음")

    # fixed.config 키도 allowed_config_fields에 포함되어야 함
    for key in fixed_config.keys():
        if key not in allowed:
            raise TemplateSchemaError(f"{tid}: fixed.config.{key}가 allowed_config_fields에 없음")


def load_templates(force: bool = False, tool_keys: set | None = None) -> dict:
    """모든 템플릿을 로드·검증해 {id: template} dict로 반환한다(캐시).
    tool_keys 미지정 시 _TOOL_MAP에서 lazy 로드한다."""
    global _cache
    if _cache is not None and not force:
        return _cache

    if tool_keys is None:
        tool_keys = _tool_map_keys()

    templates: dict = {}
    for path in sorted(glob.glob(os.path.join(TEMPLATES_DIR, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            try:
                tpl = json.load(f)
            except json.JSONDecodeError as e:
                raise TemplateSchemaError(f"{os.path.basename(path)}: JSON 파싱 실패 - {e}")
        _validate_template(tpl, path, tool_keys)
        if tpl["id"] in templates:
            raise TemplateSchemaError(f"중복된 템플릿 id: {tpl['id']}")
        templates[tpl["id"]] = tpl

    _cache = templates
    return templates


def validate_registry(tool_keys: set | None = None) -> None:
    """레지스트리 전체를 강제 재검증한다(seed/CI 드리프트 게이트용)."""
    load_templates(force=True, tool_keys=tool_keys)


def all_templates() -> list:
    return list(load_templates().values())


def get_template(template_id: str) -> dict | None:
    return load_templates().get(template_id)


def menu_index() -> str:
    """항상층에 주입할 도구 메뉴(1줄들). 노드 존재 인지 → 검색 miss 환각 방지."""
    lines = [f"- {t['id']}: {t['menu']}" for t in load_templates().values()]
    return "\n".join(lines)


def select_by_tags(text: str, limit: int | None = None) -> list:
    """요청 텍스트에 태그가 매칭되는 템플릿을 점수 내림차순으로 반환한다(#4 검색층).
    매칭 0건이면 빈 리스트(호출측에서 상위집합/폴백 처리)."""
    low = (text or "").lower()
    scored = []
    for tpl in load_templates().values():
        score = sum(1 for tag in tpl["tags"] if tag.lower() in low)
        if score > 0:
            scored.append((score, tpl))
    scored.sort(key=lambda x: x[0], reverse=True)
    result = [t for _, t in scored]
    return result[:limit] if limit else result


def resolve_template_for_node(node: dict) -> dict | None:
    """주어진 노드에 해당하는 템플릿을 찾는다(#5 검증용).
    AI 노드는 tools의 tool_key로, 그 외는 node_type으로 매칭한다.
    매칭 실패 시 None."""
    if not isinstance(node, dict):
        return None
    templates = load_templates()
    ntype = (node.get("type") or "").upper()
    config = node.get("config")
    if not isinstance(config, dict):
        config = {}

    if ntype == "AI":
        tool_names = [
            (t.get("name") if isinstance(t, dict) else t)
            for t in (config.get("tools") or [])
        ]
        # tool_key가 일치하는 템플릿 우선
        for tpl in templates.values():
            if tpl["node_type"] == "AI" and tpl["tool_key"] and tpl["tool_key"] in tool_names:
                return tpl
        # 도구 없는 AI(능력 기반/단순 추론) → tool_key null인 AI 템플릿
        for tpl in templates.values():
            if tpl["node_type"] == "AI" and tpl["tool_key"] is None and not tool_names:
                return tpl
        return None

    # TRIGGER: triggerType로 정확히 매칭(schedule/manual/webhook)
    if ntype == "TRIGGER":
        trigger_type = config.get("triggerType")
        for tpl in templates.values():
            if tpl["node_type"] == "TRIGGER" and \
                    tpl["fixed"].get("config", {}).get("triggerType") == trigger_type:
                return tpl
        return None

    # 그 외 구조 노드(CONDITION/TRANSFORM/HTTP): node_type 일치(단일 가정)
    for tpl in templates.values():
        if tpl["node_type"] == ntype:
            return tpl
    return None


def allowed_config_fields_for_node(node: dict) -> set | None:
    """주어진 노드에 허용되는 config 필드 집합을 반환한다(#5 화이트리스트).
    템플릿이 정확히 매칭되면 그 allowed_config_fields를, 매칭 실패 시 같은 node_type
    템플릿들의 합집합을 사용한다. 해당 node_type 템플릿이 전혀 없으면 None(검증 미적용)."""
    tpl = resolve_template_for_node(node)
    if tpl is not None:
        return set(tpl["allowed_config_fields"]) | UNIVERSAL_CONFIG_FIELDS

    ntype = (node.get("type") or "").upper()
    union: set = set()
    found = False
    for t in load_templates().values():
        if t["node_type"] == ntype:
            union |= set(t["allowed_config_fields"])
            found = True
    return (union | UNIVERSAL_CONFIG_FIELDS) if found else None
