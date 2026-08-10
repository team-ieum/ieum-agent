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
    "brand",
}

_VALID_NODE_TYPES = {"TRIGGER", "AI", "HTTP", "CONDITION", "TRANSFORM"}
_VALID_SLOT_KINDS = {"string", "enum", "provider", "model", "cron", "expr", "mapping", "http_method"}
# LLM이 값을 쓰지 못하고 시스템이 요청 provider에서 계산해 주입하는 슬롯 kind.
_SYSTEM_INJECTED_KINDS = {"provider", "model"}

# FE 노드 카드가 앱 아이콘/라벨을 그릴 때 쓰는 표시용 메타. 실행 경로는 읽지 않는다.
# 템플릿 fixed.config에 상수로 박히며, 앱과 무관한 AI 노드에는 아예 없다.
VALID_SERVICE_TYPES = {"GOOGLE", "NOTION", "GITHUB", "SLACK", "DISCORD"}
_REQUIRED_TOP_KEYS = {"id", "node_type", "tool_key", "tags", "menu", "fixed", "slots", "allowed_config_fields"}

# resolve_template_for_node가 매칭 템플릿을 찾지 못한 노드용 센티넬 templateId.
# dehydrate_node가 None(드롭) 대신 이 templateId를 단 draft를 반환해 MODIFY 왕복에서 노드가
# 소실되지 않게 한다.
#
# **draft["node"]에는 식별용 필드(type·label·description)만 담고, 복원은 hydrate_node가 호출부에서
# 받은 passthrough_originals에서만 한다.** draft는 LLM 프롬프트에 실려 나가고 LLM이 그대로 되돌려
# 보내는 값이라 신뢰 대상이 아니다 — config를 담으면 저장된 credentialId·토큰이 외부 LLM으로
# 나가고, 복원에 쓰면 슬롯 검증이 통째로 우회된다. 둘 다 실제로 지적됐던 경로다.
PASSTHROUGH_TEMPLATE_ID = "__passthrough__"

# 모듈 캐시 (파일은 기동 중 불변)
_cache: dict | None = None


class TemplateSchemaError(ValueError):
    """템플릿 스키마/드리프트 검증 실패"""
    pass


class SlotFillError(ValueError):
    """노드 draft(templateId+slots) 하이드레이션 실패(미존재 템플릿/누락·미허용 슬롯 등)"""
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

    # fixed.config.tools[*].name 도 _TOOL_MAP에 존재해야 함.
    # "mcp"는 동적 도구 센티넬(런타임 catalog 주입)로 _TOOL_MAP에 없어도 허용한다.
    for tool in fixed_config.get("tools", []) or []:
        name = tool.get("name") if isinstance(tool, dict) else tool
        if name and name != "mcp" and name not in tool_keys:
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

    service_type = fixed_config.get("serviceType")
    if service_type is not None and service_type not in VALID_SERVICE_TYPES:
        raise TemplateSchemaError(
            f"{tid}: fixed.config.serviceType '{service_type}' 유효하지 않음 {sorted(VALID_SERVICE_TYPES)}")


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


def template_ids() -> set:
    """등록된 모든 템플릿 id 집합. Planner가 고른 templateId 존재 검증에 쓴다."""
    return set(load_templates().keys())


def node_type_of_template(template_id: str) -> str | None:
    """templateId의 node_type(TRIGGER/AI/...)을 반환한다. 없으면 None."""
    tpl = load_templates().get(template_id)
    return tpl["node_type"] if tpl else None


def slot_catalog_text() -> str:
    """templateId별 slot 스펙 카탈로그 텍스트(Builder의 draft 작성용).

    각 줄: `- <id> [<node_type>] — <menu>` 다음 줄에 slots 명세.
    provider 슬롯은 시스템이 자동 주입하므로 '자동주입'으로 표기해 Builder가 채우지 않게 한다."""
    lines = []
    for t in load_templates().values():
        slot_specs = []
        for s in t["slots"]:
            if s["kind"] in _SYSTEM_INJECTED_KINDS:
                tag = "자동주입(작성금지)"
            else:
                tag = "필수" if s["required"] else "선택"
            slot_specs.append(f"{s['name']}({s['kind']},{tag})")
        lines.append(f"- {t['id']} [{t['fixed']['type']}] — {t['menu']}\n    slots: {', '.join(slot_specs)}")
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
        # MCP 노드: tool_key가 없는 'mcp' 동적 센티넬을 가지므로 위 매칭에 안 잡힌다.
        # ai.mcp 의사 템플릿으로 직접 해석해 dehydrate(MODIFY 역변환) 시 누락되지 않게 한다.
        if "mcp" in tool_names:
            tpl = templates.get("ai.mcp")
            if tpl is not None:
                return tpl
        # 도구 없는 AI → tool_key null인 AI 템플릿. 후보가 여럿(예: github 서브에이전트 vs 순수 추론)이면
        # fixed.config.agentType로 판별한다(github=react, ai.reasoning=simple). 일치 없으면 simple(추론) 우선.
        if not tool_names:
            candidates = [t for t in templates.values()
                          if t["node_type"] == "AI" and t["tool_key"] is None]
            if not candidates:
                return None
            node_agent = config.get("agentType")
            for tpl in candidates:
                if (tpl["fixed"].get("config") or {}).get("agentType") == node_agent:
                    return tpl
            for tpl in candidates:
                if (tpl["fixed"].get("config") or {}).get("agentType") == "simple":
                    return tpl
            return candidates[0]
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


def subagent_service_for_node(node: dict) -> str | None:
    """tool 없는 AI 노드의 동적 서브에이전트 서비스명을 intent(라벨+프롬프트) 태그 매칭으로 도출한다.

    select_by_tags 최상위 AI 템플릿이 서브에이전트형(tool_key=None)이고 service 필드가 있으면
    그 service를, 그렇지 않으면 None을 반환한다. select_by_tags 태그 매칭 신호를 써서
    '생성 시 github로 분류된 노드'와 'github brand를 받는 노드'가 정확히 일치하도록 한다.
    brand 도출(tools.registry.brand_for_node)이 tool_key 없는 github 노드를 식별하는 데 쓴다."""
    if not isinstance(node, dict) or (node.get("type") or "").upper() != "AI":
        return None
    cfg = node.get("config")
    if not isinstance(cfg, dict) or cfg.get("tools"):
        return None  # 도구가 있으면 tool_key 기반으로 brand가 도출된다
    text = f"{node.get('label', '')} {cfg.get('prompt', '')}"
    for tpl in select_by_tags(text):  # score 내림차순
        if tpl["node_type"] == "AI":
            return tpl.get("service") if tpl["tool_key"] is None else None
    return None


def _set_by_path(obj: dict, path: str, value) -> None:
    """dotted path 위치에 value를 설정한다(in-place). 숫자 세그먼트는 list 인덱스로 처리한다.
    예: "label" → obj["label"], "config.prompt" → obj["config"]["prompt"],
        "config.tools.0.config.catalogId" → obj["config"]["tools"][0]["config"]["catalogId"].
    중간 컨테이너가 없으면 다음 세그먼트 종류(숫자=list, 그 외=dict)에 맞춰 생성한다."""
    parts = path.split(".")
    cur = obj
    for i, raw in enumerate(parts[:-1]):
        nxt_is_idx = parts[i + 1].isdigit()
        if raw.isdigit():
            idx = int(raw)
            while len(cur) <= idx:
                cur.append({})
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = [] if nxt_is_idx else {}
            cur = cur[idx]
        else:
            if raw not in cur or not isinstance(cur[raw], (dict, list)):
                cur[raw] = [] if nxt_is_idx else {}
            cur = cur[raw]
    last = parts[-1]
    if last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


def hydrate_node(
    draft: dict,
    provider: str | None = None,
    passthrough_originals: dict | None = None,
) -> dict:
    """draft({id, templateId, slots})를 템플릿으로 완성된 노드로 변환한다.

    구조(type/fixed.config)는 템플릿이 결정론적으로 제공하고, 가변값만 slots에서 채운다.
    - templateId가 PASSTHROUGH_TEMPLATE_ID면 **passthrough_originals[draft["id"]]에서만** 복원한다.
      draft["node"]는 LLM 출력이라 신뢰하지 않는다(신뢰하면 슬롯 검증을 통째로 우회당한다).
      passthrough_originals를 넘기지 않은 호출부에서는 pass-through가 항상 거부된다 — 기본값이
      거부여야 새 호출부가 생겨도 안전하게 실패한다(생성 경로가 이 기본값에 기대고 있다).
      단 원본에 description이 없고 draft["node"]에 있으면 description **한 필드만** 가져온다
      (레거시+템플릿 미매칭 노드가 BE의 description 필수 검증에 걸려 저장이 막히는 것 방지).
    - templateId가 레지스트리에 없으면 SlotFillError(노드 날조 차단).
    - 템플릿에 없는 슬롯 키, 필수 슬롯 누락이면 SlotFillError(필드 날조 차단).
    - provider/model 슬롯(kind=provider|model)은 인자 provider에서 계산해 자동 주입한다
      (LLM이 채우지 않음. slots에 값이 있어도 무시하고 덮어쓴다 — 모델명 날조 차단).
    의미 검증(llmProvider/cron/tool/참조 등)은 호출부의 WorkflowValidator가 담당한다."""
    if not isinstance(draft, dict):
        raise SlotFillError("노드 draft는 객체여야 합니다.")
    tid = draft.get("templateId")
    if tid == PASSTHROUGH_TEMPLATE_ID:
        node_id = draft.get("id")
        original = (passthrough_originals or {}).get(node_id) if node_id else None
        if not isinstance(original, dict):
            raise SlotFillError(
                f"pass-through 노드(id={node_id!r})의 서버 측 원본을 찾을 수 없습니다. "
                "pass-through 노드는 새로 만들 수 없습니다."
            )
        node = copy.deepcopy(original)
        node["id"] = node_id
        # credentialId는 런타임에 백엔드가 주입한다 — 템플릿 경로가 fixed로 ""를 박는 것과 같은
        # 규칙을 여기에도 적용한다. 이 노드는 서버 저장분이 아니라 **요청 바디**에서 온 값이라
        # (agent는 워크플로우를 DB에서 읽지 않는다) 값을 그대로 되살리면 남의 credentialId를
        # 실어 보내는 경로가 된다. 저장분에 UUID가 남아 있는 노드도 여기서 정리된다.
        cfg = node.get("config")
        if isinstance(cfg, dict) and "credentialId" in cfg:
            cfg["credentialId"] = ""
        if not str(node.get("description") or "").strip():
            llm_node = draft.get("node")
            desc = llm_node.get("description") if isinstance(llm_node, dict) else None
            if not (isinstance(desc, str) and desc.strip()):
                # 모델이 안 채우면 label로 떨어진다(슬롯 경로의 backfill_legacy_description과 같은 처리).
                # label까지 비었으면 고정 문구를 쓴다 — 빈 채로 내보내면 BE의 description 필수 검증에
                # 걸려 저장 시점에 수정 결과가 통째로 날아간다(사용자는 원인을 알 수 없다).
                desc = node.get("label")
            if not (isinstance(desc, str) and desc.strip()):
                desc = "이 노드가 하는 일을 설명해요."
            node["description"] = desc.strip()
        return node
    templates = load_templates()
    tpl = templates.get(tid)
    if tpl is None:
        raise SlotFillError(f"존재하지 않는 templateId '{tid}'. 사용 가능: {sorted(templates)}")
    slots_in = draft.get("slots")
    if slots_in is None:
        slots_in = {}
    if not isinstance(slots_in, dict):
        raise SlotFillError(f"'{tid}'의 slots는 객체(dict)여야 합니다.")

    node = {
        "id": draft.get("id"),
        "type": tpl["fixed"]["type"],
        "config": copy.deepcopy(tpl["fixed"].get("config") or {}),
    }

    slot_by_name = {s["name"]: s for s in tpl["slots"]}
    unknown = set(slots_in) - set(slot_by_name)
    if unknown:
        raise SlotFillError(f"'{tid}'에 없는 슬롯: {sorted(unknown)}. 허용: {sorted(slot_by_name)}")

    for name, slot in slot_by_name.items():
        if slot["kind"] in _SYSTEM_INJECTED_KINDS:
            if provider is not None:
                # 시스템 자동 주입(요청 provider 계승). model은 그 provider의 기본 모델로 해석한다.
                # core.provider_config는 settings(MONGODB_URL 필수)를 끌어오므로, 이 파일의
                # lazy-import 관례대로 주입 분기 안에서만 import한다(env 없는 템플릿 로더 보호).
                from core.provider_config import resolve_model
                value = provider if slot["kind"] == "provider" else resolve_model(provider)
            elif slot["kind"] == "provider" and name in slots_in:
                value = slots_in[name]
            else:
                raise SlotFillError(f"'{tid}'의 {slot['kind']} 슬롯 '{name}' 주입 실패: provider 미지정")
        elif name in slots_in and not (
            slot["kind"] == "string" and slot["required"]
            and isinstance(slots_in[name], str) and not slots_in[name].strip()
        ):
            # 필수 문자열 슬롯에 빈 값/공백을 넣은 것은 '채웠다'가 아니다. 키 존재만 보면
            # description=""가 통과해 응답은 200으로 나가고, 저장 시점에 BE NodeDto의
            # @NotBlank로 400이 난다 — 사용자가 원인을 알 수 없는 자리에서 실패한다.
            value = slots_in[name]
        elif slot["required"]:
            raise SlotFillError(f"'{tid}'의 필수 슬롯 '{name}'이(가) 누락되었습니다.")
        else:
            continue  # optional 미제공 → fixed.config 기본값 유지
        _set_by_path(node, slot["path"], value)

    return node


def hydrate_nodes(
    drafts: list,
    provider: str | None = None,
    passthrough_originals: dict | None = None,
) -> list:
    """draft 리스트를 하이드레이션한다. id 누락 시 node-N 순차 부여한다.
    passthrough_originals 미지정 시 pass-through draft는 거부된다(hydrate_node 참고)."""
    if not isinstance(drafts, list):
        raise SlotFillError("nodes는 리스트여야 합니다.")
    nodes = []
    for idx, draft in enumerate(drafts):
        node = hydrate_node(draft, provider=provider, passthrough_originals=passthrough_originals)
        if not node.get("id"):
            node["id"] = f"node-{idx + 1}"
        nodes.append(node)
    return nodes


def _get_by_path(obj, path: str):
    """dotted path(숫자=list 인덱스) 위치의 값을 읽는다. 없으면 None."""
    cur = obj
    for raw in path.split("."):
        if raw.isdigit():
            idx = int(raw)
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or raw not in cur:
                return None
            cur = cur[raw]
    return cur


def dehydrate_node(node: dict) -> dict | None:
    """완성된 노드(full-node)를 draft({id, templateId, slots})로 역변환한다(MODIFY 편집용).

    resolve_template_for_node로 templateId를 찾고, 각 슬롯의 path에서 현재 값을 읽어 slots를 구성한다.
    provider/model 슬롯은 시스템이 자동 주입하므로 제외한다. 매칭 템플릿이 없으면 노드를 버리지 않고
    pass-through draft({"id", "templateId": PASSTHROUGH_TEMPLATE_ID, "node"})를 반환한다.
    **이때 "node"에는 LLM이 노드를 식별할 만큼(type·label·description)만 담는다** — 복원은
    hydrate_node가 서버 측 원본에서 하므로 config는 필요 없고, 실으면 저장된 credentialId·토큰이
    프롬프트로 외부 LLM에 나간다. node 자체가 dict가 아니면 None(역변환 불가)."""
    if not isinstance(node, dict):
        return None
    tpl = resolve_template_for_node(node)
    if tpl is None:
        # 매칭 실패는 노드가 사라지지는 않지만 '편집 불가'로 강등되는 사건이라 흔적을 남긴다.
        # 템플릿 tool_key나 _TOOL_MAP 키를 바꿔 흔한 노드가 매칭에서 빠지면 수정 요청이 전부
        # "편집을 지원하지 않는다"로 끝나는데, 로그가 없으면 사용자 신고 전까지 알 수 없다.
        logger.info("pass-through 강등 — 매칭 템플릿 없음 (node_id=%s, type=%s, tools=%s)",
                    node.get("id"), node.get("type"),
                    [t.get("name") if isinstance(t, dict) else t
                     for t in ((node.get("config") or {}).get("tools") or [])])
        # 복원은 서버가 쥔 원본으로만 한다(hydrate_node 참고). 그래서 draft에는 LLM이 이 노드를
        # 식별하는 데 필요한 만큼만 담는다 — config를 통째로 실으면 저장된 credentialId·토큰
        # 같은 값이 프롬프트로 외부 LLM에 나가는데, 서버는 그 값을 쓰지도 않는다.
        return {
            "id": node.get("id"),
            "templateId": PASSTHROUGH_TEMPLATE_ID,
            "node": {
                "type": node.get("type"),
                "label": node.get("label"),
                "description": node.get("description"),
            },
        }
    slots = {}
    for s in tpl["slots"]:
        if s["kind"] in _SYSTEM_INJECTED_KINDS:
            continue
        val = _get_by_path(node, s["path"])
        if val not in (None, ""):
            slots[s["name"]] = val
    return {"id": node.get("id"), "templateId": tpl["id"], "slots": slots}


def dehydrate_nodes(nodes: list) -> list:
    """노드 리스트를 draft 리스트로 역변환한다. 템플릿 매칭 실패 노드도 pass-through draft로 보존하므로
    아무 노드도 버리지 않는다(node가 dict가 아닌 항목만 건너뛴다)."""
    if not isinstance(nodes, list):
        return []
    return [d for d in (dehydrate_node(n) for n in nodes) if d is not None]


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
