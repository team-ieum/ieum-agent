"""노드 템플릿 레지스트리 (SSOT 로더)

ieum-workflow-design/templates/*.json 을 단일 진실 원천으로 로드·검증한다.
스키마 명세는 templates/SCHEMA.md 참고.

소비처:
- skill_loader(#4): menu_index() + select_by_tags() 로 항상층/검색층 주입
- workflow_validator(#5): allowed_config_fields() 로 config 필드 화이트리스트
- seed 스크립트(#3): all_templates() 를 MongoDB로 동기화
"""
import os
import copy
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


def resolve_tool_key_by_intent(text: str) -> str | None:
    """의도 텍스트(role/description/label/prompt)를 태그 매칭해 가장 적합한 AI 템플릿의
    tool_key를 반환한다. 매칭 없거나, 최상위 AI 템플릿이 서브에이전트형(tool_key=None,
    예: ai.github_query)이면 None을 반환한다(빈 tools 유지 = 실행 시 서브에이전트 처리)."""
    for tpl in select_by_tags(text):  # score 내림차순
        if tpl["node_type"] == "AI":
            return tpl["tool_key"]  # 명시도구형은 키, 서브에이전트형은 None
    return None


def tool_key_to_config_tool(key: str) -> dict:
    """plan/도구키 문자열을 노드 config.tools 항목 형식으로 변환한다.
    'mcp:<catalogId>' 또는 'mcp'는 실행 주입 형식으로, 그 외는 {"name": key}로 변환."""
    if key == "mcp" or (isinstance(key, str) and key.startswith("mcp:")):
        catalog_id = key[len("mcp:"):] if key.startswith("mcp:") else ""
        return {"name": "mcp", "config": {"catalogId": catalog_id}}
    return {"name": key}


def backfill_empty_ai_tools(nodes: list) -> None:
    """빈 tools를 가진 react AI 노드에 한해, 라벨+프롬프트 의도로 명시도구형 템플릿을
    해석해 tool_key를 결정론적으로 주입한다(in-place).

    - react + 빈 tools만 대상: simple(순수 추론)은 건드리지 않는다.
    - 서브에이전트형(github 등, tool_key=None) 또는 매칭 없음이면 빈 채로 둔다.
    plan이 없는 chat 경로의 결정론 보강용(plan 경로는 plan tools 강제 주입으로 처리)."""
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict) or (node.get("type") or "").upper() != "AI":
            continue
        cfg = node.get("config")
        if not isinstance(cfg, dict):
            continue
        if cfg.get("agentType") != "react" or cfg.get("tools"):
            continue
        text = f"{node.get('label', '')} {cfg.get('prompt', '')}"
        key = resolve_tool_key_by_intent(text)
        if key:
            cfg["tools"] = [tool_key_to_config_tool(key)]


def _tool_identity(tool) -> tuple:
    """도구 항목의 동일성 키. mcp는 catalogId까지 구분한다."""
    if isinstance(tool, dict):
        cfg = tool.get("config") or {}
        return (tool.get("name"), cfg.get("catalogId"))
    return (tool, None)


def _merge_fixed_tools(fixed_tools: list, existing: list | None) -> list:
    """템플릿 fixed 도구를 보장하되, 동일 도구가 이미 있으면 기존 항목을 우선한다.

    기존 항목은 런타임 config(slack/discord의 webhookCredentialId, mcp의 catalogId 등)를
    담고 있으므로 템플릿의 bare fixed 도구로 덮어쓰면 안 된다. fixed 도구가 누락된 경우에만
    주입하고, 기존의 추가 도구(mcp 등)도 보존한다."""
    existing = existing or []
    existing_by_key: dict = {}
    for tool in existing:
        existing_by_key.setdefault(_tool_identity(tool), tool)

    result: list = []
    seen: set = set()
    for tool in (fixed_tools or []):
        key = _tool_identity(tool)
        if key in seen:
            continue
        seen.add(key)
        # 동일 도구가 이미 있으면 런타임 config 보존을 위해 기존 항목을 사용
        result.append(existing_by_key.get(key, copy.deepcopy(tool)))
    for tool in existing:
        key = _tool_identity(tool)
        if key not in seen:
            seen.add(key)
            result.append(tool)
    return result


def apply_template_fixed(nodes: list) -> None:
    """각 노드에 매칭되는 템플릿의 fixed.config를 결정론적으로 강제 적용한다(in-place).

    LLM이 빠뜨리거나 잘못 채운 '보장값'(tools, agentType, credentialId, triggerType 등)을
    템플릿 SSOT로 덮어써 비결정론을 제거한다. tools는 fixed 도구를 보장하되 기존 추가 도구
    (mcp 등)는 보존하는 병합 방식을 쓴다. prompt·llmProvider 등 slot(가변값)은 fixed.config에
    없으므로 건드리지 않는다. 매칭 템플릿이 없으면 건너뛴다(검증 게이트가 별도 차단)."""
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        # 빈 tools AI 노드는 실행 시 서브에이전트(web/github/transform 등)가 자동 처리하는
        # 정상 패턴이다. tool_key로 결정론 매칭이 불가하고(빈 tools는 tool_key=None 템플릿으로
        # 폴백되어 오매칭됨), 강제할 fixed 도구도 없으므로 건너뛴다.
        if (node.get("type") or "").upper() == "AI":
            cfg = node.get("config")
            if not (isinstance(cfg, dict) and cfg.get("tools")):
                continue
        tpl = resolve_template_for_node(node)
        if tpl is None:
            continue
        fixed_config = (tpl.get("fixed") or {}).get("config") or {}
        if not fixed_config:
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            config = {}
            node["config"] = config
        for key, val in fixed_config.items():
            if key == "tools":
                config["tools"] = _merge_fixed_tools(val, config.get("tools"))
            else:
                config[key] = copy.deepcopy(val)


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
